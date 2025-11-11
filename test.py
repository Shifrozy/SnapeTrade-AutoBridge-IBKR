#!/usr/bin/env python3
"""
SnapTrade → IBKR Batch Trading Script
======================================
Reads orders from CSV and places them via SnapTrade to IBKR accounts.

USAGE:
    python trade.py --csv orders.csv [OPTIONS]

OPTIONS:
    --csv PATH          Path to orders CSV file (required)
    --dry-run           Validate and check impact only; do not place orders
    --max-retries N     Maximum retry attempts for transient errors (default: 3)
    --rate-limit N      Max API calls per second (default: 5)
    --timeout N         Request timeout in seconds (default: 30)
    --concurrency N     Number of parallel workers (default: 1, sequential)

ENVIRONMENT VARIABLES (create .env file):
    SNAPTRADE_CLIENT_ID      - Your SnapTrade partner client ID
    SNAPTRADE_CONSUMER_KEY   - Your SnapTrade consumer key
    SNAPTRADE_USER_ID        - End-user ID for authentication
    SNAPTRADE_USER_SECRET    - End-user secret for authentication

CSV SCHEMA (headers must match exactly - do not rename):
    account_id      - IBKR account identifier
    ticker          - Stock symbol (e.g., AAPL)
    side            - BUY or SELL
    quantity        - Number of shares (decimal if fractional enabled)
    order_type      - MARKET, LIMIT, STOP, or STOP_LIMIT
    limit_price     - Required for LIMIT/STOP_LIMIT; blank otherwise
    stop_price      - Required for STOP/STOP_LIMIT; blank otherwise
    time_in_force   - DAY or GTC (defaults to DAY if blank)
    exchange        - Optional (e.g., NASDAQ); helps with symbol resolution

EXAMPLE .env FILE:
    SNAPTRADE_CLIENT_ID=ABC123XYZ
    SNAPTRADE_CONSUMER_KEY=your_secret_key_here
    SNAPTRADE_USER_ID=user@example.com
    SNAPTRADE_USER_SECRET=user_secret_token

EXAMPLES:
    # Validate orders without executing
    python trade.py --csv orders.csv --dry-run

    # Execute orders sequentially
    python trade.py --csv orders.csv

    # Execute with 3 parallel workers, custom timeout
    python trade.py --csv orders.csv --concurrency 3 --timeout 60

OUTPUT FILES:
    execution.log   - Detailed log of all operations
    results.csv     - Per-row results with status and broker order IDs

Author: SnapTrade Integration Team
"""

import os
import sys
import argparse
import csv
import hashlib
import logging
import time
from typing import List, Dict, Optional, Set, Tuple
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pprint import pprint

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Import SnapTrade SDK
try:
    from snaptrade_client import SnapTrade
    from snaptrade_client.exceptions import ApiException
except ImportError as e:
    print("❌ ERROR: SnapTrade SDK not installed!")
    print("\nPlease install it with:")
    print("    pip install snaptrade-python-sdk python-dotenv")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════
# LOGGING CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

LOG_FORMAT = '%(asctime)s | %(levelname)-8s | %(message)s'
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler('execution.log', encoding='utf-8', mode='a'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

# SnapTrade Configuration (loaded from .env)
SNAPTRADE_CLIENT_ID = os.getenv("SNAPTRADE_CLIENT_ID")
SNAPTRADE_CONSUMER_KEY = os.getenv("SNAPTRADE_CONSUMER_KEY")
SNAPTRADE_USER_ID = os.getenv("SNAPTRADE_USER_ID")
SNAPTRADE_USER_SECRET = os.getenv("SNAPTRADE_USER_SECRET")

def print_header(title: str):
    """Print a formatted header."""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)

def print_section(title: str):
    """Print a formatted section."""
    print(f"\n--- {title} ---")

def validate_credentials():
    """Validate that all required credentials are present."""
    print_header("Validating Credentials")
    
    required_vars = {
        "SNAPTRADE_CLIENT_ID": SNAPTRADE_CLIENT_ID,
        "SNAPTRADE_CONSUMER_KEY": SNAPTRADE_CONSUMER_KEY,
        "SNAPTRADE_USER_ID": SNAPTRADE_USER_ID,
        "SNAPTRADE_USER_SECRET": SNAPTRADE_USER_SECRET,
    }
    
    missing = [key for key, value in required_vars.items() if not value]
    
    if missing:
        logger.error(f"❌ Missing required environment variables:")
        for var in missing:
            logger.error(f"   - {var}")
        logger.error("\nPlease check your .env file!")
        sys.exit(1)
    
    logger.info("✅ All credentials present")
    return True

# ═══════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════

@dataclass
class OrderRow:
    """Represents a validated order from the CSV."""
    row_num: int
    account_id: str
    ticker: str
    side: str
    quantity: Decimal
    order_type: str
    limit_price: Optional[Decimal]
    stop_price: Optional[Decimal]
    time_in_force: str
    exchange: Optional[str]
    
    def idempotency_key(self) -> str:
        """
        Generate unique idempotency key for this order.
        Prevents duplicate submissions within the same run.
        """
        components = [
            self.account_id,
            self.ticker,
            self.side,
            str(self.quantity),
            self.order_type,
            str(self.limit_price or ''),
            str(self.stop_price or ''),
            self.time_in_force,
        ]
        data = '|'.join(components).encode('utf-8')
        return hashlib.sha256(data).hexdigest()[:16]


@dataclass
class OrderResult:
    """Result of processing a single order."""
    row_num: int
    status: str  # SKIPPED, VALIDATED, PLACED, FAILED
    reason: str
    broker_order_id: Optional[str] = None
    filled_qty: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dict for CSV output."""
        return {
            'input_row': self.row_num,
            'status': self.status,
            'reason': self.reason,
            'broker_order_id': self.broker_order_id or '',
            'filled_qty': self.filled_qty or '',
        }

# ═══════════════════════════════════════════════════════════════════
# SNAPTRADE CLIENT (UPDATED)
# ═══════════════════════════════════════════════════════════════════

class SnapTradeManager:
    """
    Manages SnapTrade API interactions.
    
    CHANGES FROM ORIGINAL:
    - Added rate limiting
    - Added retry logic with exponential backoff
    - Added symbol search/resolution
    - Added check_order_impact method
    - Added place_order method (two-step flow)
    - Removed order fetching (now CSV-based)
    """
    
    def __init__(self, rate_limit: int = 5, timeout: int = 30, max_retries: int = 3):
        """Initialize SnapTrade client."""
        self.client = SnapTrade(
            consumer_key=SNAPTRADE_CONSUMER_KEY,
            client_id=SNAPTRADE_CLIENT_ID,
        )
        self.user_id = SNAPTRADE_USER_ID
        self.user_secret = SNAPTRADE_USER_SECRET
        self.rate_limit = rate_limit
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_request_time = 0
    
    def _apply_rate_limit(self):
        """Simple rate limiting - ensure minimum gap between requests."""
        if self.rate_limit > 0:
            min_gap = 1.0 / self.rate_limit
            elapsed = time.time() - self._last_request_time
            if elapsed < min_gap:
                time.sleep(min_gap - elapsed)
        self._last_request_time = time.time()
    
    def _call_with_retry(self, func, *args, **kwargs):
        """
        Call a SnapTrade API function with exponential backoff retry.
        Retries on: network errors, rate limits, server errors
        Does NOT retry on: 4xx client errors
        """
        last_exception = None
        
        for attempt in range(self.max_retries + 1):
            try:
                self._apply_rate_limit()
                result = func(*args, **kwargs)
                return result
                
            except ApiException as e:
                last_exception = e
                
                # Don't retry on 4xx client errors (except 429)
                if hasattr(e, 'status'):
                    if 400 <= e.status < 500 and e.status != 429:
                        logger.error(f"Client error {e.status}: {e}")
                        raise
                    
                    # Retry on rate limiting
                    if e.status == 429:
                        wait = 2 ** attempt
                        logger.warning(f"Rate limited (429). Waiting {wait}s before retry...")
                        time.sleep(wait)
                        continue
                    
                    # Retry on server errors
                    if 500 <= e.status < 600:
                        if attempt < self.max_retries:
                            wait = 2 ** attempt
                            logger.warning(f"Server error {e.status}. Retrying in {wait}s... (attempt {attempt + 1}/{self.max_retries})")
                            time.sleep(wait)
                            continue
                
                # Generic retry for other ApiExceptions
                if attempt < self.max_retries:
                    wait = 2 ** attempt
                    logger.warning(f"API error: {e}. Retrying in {wait}s... (attempt {attempt + 1}/{self.max_retries})")
                    time.sleep(wait)
                else:
                    logger.error(f"API call failed after {self.max_retries + 1} attempts: {e}")
                    raise
                    
            except Exception as e:
                last_exception = e
                if attempt < self.max_retries:
                    wait = 2 ** attempt
                    logger.warning(f"Request failed: {e}. Retrying in {wait}s... (attempt {attempt + 1}/{self.max_retries})")
                    time.sleep(wait)
                else:
                    logger.error(f"Request failed after {self.max_retries + 1} attempts: {e}")
                    raise
        
        if last_exception:
            raise last_exception
    
    def test_connection(self) -> bool:
        """Test API connection."""
        print_section("Testing SnapTrade Connection")
        try:
            response = self._call_with_retry(self.client.api_status.check)
            logger.info("✅ Connected to SnapTrade API")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to connect to SnapTrade: {str(e)}")
            return False
    
    def search_symbols(self, ticker: str, exchange: Optional[str] = None) -> List[dict]:
        """
        Search for symbols matching ticker.
        If exchange is provided, filters to that exchange.
        
        NEW METHOD - Added for CSV symbol resolution
        """
        try:
            # SnapTrade symbol search
            response = self._call_with_retry(
                self.client.trading.get_user_account_quotes,
                user_id=self.user_id,
                user_secret=self.user_secret,
                symbols=ticker,
                use_ticker=True
            )
            
            # Parse response
            if hasattr(response, 'body'):
                symbols = response.body if isinstance(response.body, list) else [response.body]
            else:
                symbols = []
            
            # Filter by exchange if specified
            if exchange and symbols:
                exchange_upper = exchange.upper()
                symbols = [
                    s for s in symbols
                    if s.get('exchange', {}).get('code', '').upper() == exchange_upper
                ]
            
            # Prefer exact ticker matches
            if symbols:
                exact_matches = [
                    s for s in symbols
                    if s.get('symbol', '').upper() == ticker.upper()
                ]
                if exact_matches:
                    return exact_matches
            
            return symbols
            
        except Exception as e:
            logger.error(f"Symbol search failed for '{ticker}': {e}")
            return []
    
    def get_universal_symbol_id(self, ticker: str, exchange: Optional[str] = None) -> Optional[str]:
        """
        Resolve ticker to universal_symbol_id.
        Returns None if symbol not found or ambiguous.
        
        NEW METHOD - Added for CSV symbol resolution
        """
        symbols = self.search_symbols(ticker, exchange)
        
        if not symbols:
            logger.error(f"Symbol not found: {ticker}" + (f" on {exchange}" if exchange else ""))
            return None
        
        if len(symbols) > 1:
            logger.warning(f"Multiple symbols found for '{ticker}':")
            for sym in symbols[:5]:  # Show first 5
                exch = sym.get('exchange', {}).get('code', 'UNKNOWN')
                logger.warning(f"  - {sym.get('symbol')} on {exch}")
            
            if not exchange:
                logger.error(f"Ambiguous symbol '{ticker}'. Please specify 'exchange' column in CSV.")
                return None
            
            logger.warning(f"Multiple listings on {exchange}. Using first match.")
        
        symbol_id = symbols[0].get('id') or symbols[0].get('universal_symbol_id')
        symbol_name = symbols[0].get('symbol', ticker)
        exch_code = symbols[0].get('exchange', {}).get('code', '')
        
        logger.info(f"✓ Resolved '{ticker}' → {symbol_name} ({exch_code}) [id={symbol_id}]")
        return symbol_id
    
    def check_order_impact(self, order: OrderRow, symbol_id: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Check order impact (pre-validation with broker).
        Returns: (success, trade_id, error_message)
        
        NEW METHOD - Two-step order flow (step 1)
        """
        try:
            # Build order parameters
            order_params = {
                'account_id': order.account_id,
                'action': order.side,
                'order_type': order.order_type,
                'quantity': float(order.quantity),
                'universal_symbol_id': symbol_id,
                'time_in_force': order.time_in_force,
            }
            
            # Add price fields based on order type
            if order.order_type in ('LIMIT', 'STOP_LIMIT'):
                if order.limit_price:
                    order_params['price'] = float(order.limit_price)
            
            if order.order_type in ('STOP', 'STOP_LIMIT'):
                if order.stop_price:
                    order_params['stop'] = float(order.stop_price)
            
            # Call impact check API
            response = self._call_with_retry(
                self.client.trading.get_order_impact,
                user_id=self.user_id,
                user_secret=self.user_secret,
                **order_params
            )
            
            # Extract trade_id from response
            if hasattr(response, 'body'):
                data = response.body
            else:
                data = response
            
            trade_id = data.get('trade_id') or data.get('tradeId')
            
            if not trade_id:
                logger.error(f"Impact check response missing trade_id: {data}")
                return False, None, "No trade_id in impact check response"
            
            logger.info(f"✓ Impact check passed [trade_id={trade_id}]")
            return True, trade_id, None
            
        except ApiException as e:
            error_text = str(e)
            logger.error(f"Impact check API error: {error_text}")
            return False, None, f"Impact check failed: {error_text[:200]}"
            
        except Exception as e:
            logger.error(f"Impact check error: {e}")
            return False, None, f"Impact check error: {str(e)}"
    
    def place_order(self, trade_id: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Place order using trade_id from impact check.
        Returns: (success, broker_order_id, error_message)
        
        NEW METHOD - Two-step order flow (step 2)
        """
        try:
            # Call place order API
            response = self._call_with_retry(
                self.client.trading.place_order,
                trade_id=trade_id,
                user_id=self.user_id,
                user_secret=self.user_secret
            )
            
            # Extract order ID from response
            if hasattr(response, 'body'):
                data = response.body
            else:
                data = response
            
            order_id = (
                data.get('order_id') or
                data.get('orderId') or
                data.get('id') or
                str(data.get('order', {}).get('id', ''))
            )
            
            if not order_id:
                logger.warning(f"Order placed but no order_id in response: {data}")
                order_id = trade_id  # Fallback to trade_id
            
            logger.info(f"✓ Order placed [broker_order_id={order_id}]")
            return True, order_id, None
            
        except ApiException as e:
            error_text = str(e)
            logger.error(f"Order placement API error: {error_text}")
            return False, None, f"Broker rejected: {error_text[:200]}"
            
        except Exception as e:
            logger.error(f"Order placement error: {e}")
            return False, None, f"Placement error: {str(e)}"
    
    def refresh_account(self, account_id: str):
        """
        Trigger account data refresh (best-effort, non-blocking).
        
        NEW METHOD - Post-order refresh
        """
        try:
            self._call_with_retry(
                self.client.account_information.get_user_account_details,
                account_id=account_id,
                user_id=self.user_id,
                user_secret=self.user_secret
            )
            logger.debug(f"Triggered account refresh for {account_id}")
        except Exception as e:
            logger.debug(f"Account refresh failed (non-critical): {e}")

# ═══════════════════════════════════════════════════════════════════
# CSV PARSING & VALIDATION (NEW SECTION)
# ═══════════════════════════════════════════════════════════════════

def parse_decimal(value: str, field_name: str) -> Optional[Decimal]:
    """Parse string to Decimal, handling empty/whitespace values."""
    if not value or not value.strip():
        return None
    
    try:
        return Decimal(value.strip())
    except (InvalidOperation, ValueError):
        logger.warning(f"Invalid decimal for {field_name}: '{value}'")
        return None


def validate_order_row(row_dict: dict, row_num: int) -> Tuple[Optional[OrderRow], Optional[str]]:
    """
    Validate a single CSV row.
    Returns: (OrderRow, error_message) - exactly one will be None.
    
    NEW FUNCTION - CSV validation logic
    """
    # Trim all string values
    row_dict = {k: v.strip() if isinstance(v, str) else v for k, v in row_dict.items()}
    
    # Required fields check
    required = ['ticker', 'side', 'quantity', 'order_type']
    missing = [f for f in required if not row_dict.get(f)]
    if missing:
        return None, f"Missing required fields: {', '.join(missing)}"
    
    # Validate SIDE
    side = row_dict['side'].upper()
    if side not in ('BUY', 'SELL'):
        return None, f"Invalid side '{side}'. Must be BUY or SELL"
    
    # Validate ORDER_TYPE
    order_type = row_dict['order_type'].upper()
    valid_types = ('MARKET', 'LIMIT', 'STOP', 'STOP_LIMIT')
    if order_type not in valid_types:
        return None, f"Invalid order_type '{order_type}'. Must be one of: {', '.join(valid_types)}"
    
    # Parse QUANTITY
    quantity = parse_decimal(row_dict['quantity'], 'quantity')
    if quantity is None:
        return None, "Invalid or missing quantity"
    if quantity <= 0:
        return None, f"Quantity must be positive, got {quantity}"
    
    # Parse prices
    limit_price = parse_decimal(row_dict.get('limit_price', ''), 'limit_price')
    stop_price = parse_decimal(row_dict.get('stop_price', ''), 'stop_price')
    
    # Validate price requirements by order type
    if order_type == 'LIMIT':
        if limit_price is None or limit_price <= 0:
            return None, "LIMIT order requires a valid limit_price > 0"
    
    elif order_type == 'STOP':
        if stop_price is None or stop_price <= 0:
            return None, "STOP order requires a valid stop_price > 0"
    
    elif order_type == 'STOP_LIMIT':
        if limit_price is None or limit_price <= 0:
            return None, "STOP_LIMIT order requires a valid limit_price > 0"
        if stop_price is None or stop_price <= 0:
            return None, "STOP_LIMIT order requires a valid stop_price > 0"
    
    # TIME_IN_FORCE - default to DAY if blank
    tif = row_dict.get('time_in_force', '').upper() or 'DAY'
    if tif not in ('DAY', 'GTC'):
        return None, f"Invalid time_in_force '{tif}'. Must be DAY or GTC"
    
    # Optional fields
    account_id = row_dict.get('account_id', '').strip()
    exchange = row_dict.get('exchange', '').strip() or None
    
    # Create validated OrderRow
    order = OrderRow(
        row_num=row_num,
        account_id=account_id,
        ticker=row_dict['ticker'].upper(),
        side=side,
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
        stop_price=stop_price,
        time_in_force=tif,
        exchange=exchange,
    )
    
    return order, None


def load_orders_from_csv(csv_path: Path) -> List[Tuple[int, dict]]:
    """
    Load orders from CSV file.
    Returns list of (row_number, row_dict) tuples.
    
    NEW FUNCTION - CSV loading with validation
    """
    if not csv_path.exists():
        logger.error(f"CSV file not found: {csv_path}")
        sys.exit(1)
    
    orders = []
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            # Validate required headers are present
            required_headers = {
                'account_id', 'ticker', 'side', 'quantity', 'order_type',
                'limit_price', 'stop_price', 'time_in_force', 'exchange'
            }
            
            if not reader.fieldnames:
                logger.error("CSV file is empty or has no headers")
                sys.exit(1)
            
            actual_headers = set(reader.fieldnames)
            missing = required_headers - actual_headers
            
            if missing:
                logger.error(f"CSV missing required headers: {', '.join(sorted(missing))}")
                logger.error(f"Expected headers: {', '.join(sorted(required_headers))}")
                logger.error(f"Found headers: {', '.join(sorted(actual_headers))}")
                sys.exit(1)
            
            # Read rows
            for row_num, row in enumerate(reader, start=2):  # Row 1 is header
                # Skip completely blank rows
                if not any(row.values()):
                    logger.debug(f"Row {row_num}: Skipping blank row")
                    continue
                
                # Skip comment rows (ticker starts with #)
                ticker = row.get('ticker', '').strip()
                if ticker.startswith('#'):
                    logger.info(f"Row {row_num}: Skipping comment row")
                    continue
                
                orders.append((row_num, row))
        
        logger.info(f"✓ Loaded {len(orders)} order rows from {csv_path}")
        return orders
        
    except Exception as e:
        logger.error(f"Failed to read CSV file: {e}")
        sys.exit(1)

# ═══════════════════════════════════════════════════════════════════
# ORDER PROCESSING (NEW SECTION)
# ═══════════════════════════════════════════════════════════════════

def process_single_order(
    order: OrderRow,
    snaptrade: SnapTradeManager,
    dry_run: bool,
    seen_keys: Set[str]
) -> OrderResult:
    """
    Process a single validated order.
    This is the main execution logic for each order.
    
    NEW FUNCTION - Core order processing logic
    """
    
    logger.info(f"Row {order.row_num}: {order.side} {order.quantity} {order.ticker} @ {order.order_type}")
    
    # Check idempotency
    idem_key = order.idempotency_key()
    if idem_key in seen_keys:
        msg = "Duplicate order detected (same parameters already processed in this run)"
        logger.warning(f"Row {order.row_num}: {msg}")
        return OrderResult(
            row_num=order.row_num,
            status='SKIPPED',
            reason=msg
        )
    seen_keys.add(idem_key)
    
    # Step 1: Resolve ticker to universal_symbol_id
    symbol_id = snaptrade.get_universal_symbol_id(order.ticker, order.exchange)
    if not symbol_id:
        return OrderResult(
            row_num=order.row_num,
            status='FAILED',
            reason=f"Symbol not found: {order.ticker}" + (f" on {order.exchange}" if order.exchange else "")
        )
    
    # Step 2: Check order impact (pre-validation with broker)
    success, trade_id, error = snaptrade.check_order_impact(order, symbol_id)
    if not success:
        return OrderResult(
            row_num=order.row_num,
            status='FAILED',
            reason=f"Impact check failed: {error}"
        )
    
    # If dry-run, stop here
    if dry_run:
        logger.info(f"Row {order.row_num}: [DRY-RUN] Impact check passed, would place with trade_id={trade_id}")
        return OrderResult(
            row_num=order.row_num,
            status='VALIDATED',
            reason='Dry-run: impact check passed, order not placed'
        )
    
    # Step 3: Place the order
    success, broker_order_id, error = snaptrade.place_order(trade_id)
    if not success:
        return OrderResult(
            row_num=order.row_num,
            status='FAILED',
            reason=f"Order placement failed: {error}"
        )
    
    # Step 4: Trigger account refresh (best effort, non-blocking)
    if order.account_id:
        snaptrade.refresh_account(order.account_id)
    
    logger.info(f"Row {order.row_num}: ✓ PLACED successfully [order_id={broker_order_id}]")
    
    return OrderResult(
        row_num=order.row_num,
        status='PLACED',
        reason='Order placed successfully',
        broker_order_id=broker_order_id,
    )


def process_orders(
    orders: List[Tuple[int, dict]],
    snaptrade: SnapTradeManager,
    dry_run: bool,
    concurrency: int
) -> List[OrderResult]:
    """
    Process all orders with optional parallel execution.
    
    NEW FUNCTION - Batch order processing
    """
    
    results = []
    seen_keys: Set[str] = set()  # Idempotency tracking
    
    # First pass: validate all rows
    validated_orders = []
    for row_num, row_dict in orders:
        order, error = validate_order_row(row_dict, row_num)
        
        if error:
            logger.warning(f"Row {row_num}: Validation failed - {error}")
            results.append(OrderResult(
                row_num=row_num,
                status='SKIPPED',
                reason=f"Validation error: {error}"
            ))
        else:
            validated_orders.append(order)
    
    if not validated_orders:
        logger.warning("No valid orders to process after validation")
        return results
    
    logger.info(f"Processing {len(validated_orders)} validated orders...")
    
    # Second pass: execute orders
    if concurrency > 1:
        # Parallel execution
        logger.info(f"Using {concurrency} parallel workers")
        
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_order = {
                executor.submit(process_single_order, order, snaptrade, dry_run, seen_keys): order
                for order in validated_orders
            }
            
            for future in as_completed(future_to_order):
                order = future_to_order[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    logger.exception(f"Row {order.row_num}: Unexpected error: {e}")
                    results.append(OrderResult(
                        row_num=order.row_num,
                        status='FAILED',
                        reason=f"Unexpected error: {str(e)[:200]}"
                    ))
    else:
        # Sequential execution
        logger.info("Processing orders sequentially")
        
        for order in validated_orders:
            try:
                result = process_single_order(order, snaptrade, dry_run, seen_keys)
                results.append(result)
            except Exception as e:
                logger.exception(f"Row {order.row_num}: Unexpected error: {e}")
                results.append(OrderResult(
                    row_num=order.row_num,
                    status='FAILED',
                    reason=f"Unexpected error: {str(e)[:200]}"
                ))
    
    return results

# ═══════════════════════════════════════════════════════════════════
# OUTPUT & REPORTING (NEW SECTION)
# ═══════════════════════════════════════════════════════════════════

def write_results_csv(results: List[OrderResult], output_path: Path):
    """Write execution results to CSV file."""
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['input_row', 'status', 'reason', 'broker_order_id', 'filled_qty']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        writer.writeheader()
        for result in sorted(results, key=lambda r: r.row_num):
            writer.writerow(result.to_dict())
    
    logger.info(f"✓ Results written to {output_path}")


def print_summary(results: List[OrderResult]):
    """Print execution summary to console and log."""
    status_counts = {
        'PLACED': 0,
        'VALIDATED': 0,
        'FAILED': 0,
        'SKIPPED': 0,
    }
    
    for result in results:
        status_counts[result.status] = status_counts.get(result.status, 0) + 1
    
    print_header("EXECUTION SUMMARY")
    logger.info(f"Total rows processed:  {len(results)}")
    logger.info(f"  ✅ PLACED:            {status_counts['PLACED']}")
    logger.info(f"  ✓ VALIDATED:         {status_counts['VALIDATED']}")
    logger.info(f"  ❌ FAILED:            {status_counts['FAILED']}")
    logger.info(f"  - SKIPPED:           {status_counts['SKIPPED']}")
    logger.info("=" * 70)
    
    # Log failed rows for easy review
    if status_counts['FAILED'] > 0:
        logger.info("")
        logger.info("FAILED ORDERS:")
        for result in results:
            if result.status == 'FAILED':
                logger.info(f"  Row {result.row_num}: {result.reason}")
        logger.info("")

# ═══════════════════════════════════════════════════════════════════
# MAIN EXECUTION (UPDATED)
# ═══════════════════════════════════════════════════════════════════

def main():
    """
    Main execution flow.
    
    CHANGES FROM ORIGINAL:
    - Added CLI argument parsing
    - Changed from "fetch orders" to "read CSV"
    - Removed IBKR TWS connection
    - Added results.csv output
    - Added proper exit codes
    """
    
    parser = argparse.ArgumentParser(
        description='SnapTrade → IBKR Batch Trading Script',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument('--csv', required=True, type=Path,
                        help='Path to orders CSV file (required)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Validate and check impact only; do not place orders')
    parser.add_argument('--max-retries', type=int, default=3,
                        help='Maximum retry attempts for transient errors (default: 3)')
    parser.add_argument('--rate-limit', type=int, default=5,
                        help='Max API calls per second (default: 5)')
    parser.add_argument('--timeout', type=int, default=30,
                        help='Request timeout in seconds (default: 30)')
    parser.add_argument('--concurrency', type=int, default=1,
                        help='Number of parallel workers (default: 1, sequential)')
    
    args = parser.parse_args()
    
    # Header
    print_header("SnapTrade → IBKR Batch Trading Script")
    logger.info(f"Mode: {'DRY RUN (No actual orders)' if args.dry_run else 'LIVE TRADING'}")
    logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Step 1: Validate credentials
    validate_credentials()
    
    # Step 2: Initialize SnapTrade
    print_section("Initializing SnapTrade")
    snaptrade = SnapTradeManager(
        rate_limit=args.rate_limit,
        timeout=args.timeout,
        max_retries=args.max_retries
    )
    
    if not snaptrade.test_connection():
        return 1
    
    # Step 3: Load orders from CSV
    logger.info(f"Loading orders from {args.csv}...")
    raw_orders = load_orders_from_csv(args.csv)
    
    if not raw_orders:
        logger.warning("No orders found in CSV")
        return 0
    
    # Step 4: Process orders
    if args.dry_run:
        logger.info("*** DRY-RUN MODE *** - Orders will NOT be placed")
    
    results = process_orders(
        orders=raw_orders,
        snaptrade=snaptrade,
        dry_run=args.dry_run,
        concurrency=args.concurrency
    )
    
    # Step 5: Write results to CSV
    results_path = Path('results.csv')
    write_results_csv(results, results_path)
    
    # Step 6: Print summary
    print_summary(results)
    
    # Step 7: Exit with error code if any failures
    failed_count = sum(1 for r in results if r.status == 'FAILED')
    if failed_count > 0:
        logger.error(f"Exiting with error: {failed_count} orders failed")
        return 1
    
    logger.info("✅ All orders processed successfully")
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.warning("\n⚠️  Interrupted by user (Ctrl+C)")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"❌ Unexpected error: {str(e)}")
        sys.exit(1)