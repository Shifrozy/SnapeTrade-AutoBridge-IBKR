#!/usr/bin/env python3
"""
SnapTrade to IBKR TWS Bridge
============================
Fetches open orders from SnapTrade and places them in IBKR TWS using ib_insync.

Requirements:
    pip install snaptrade-python-sdk python-dotenv ib_insync

Setup:
    1. Create a .env file with your credentials
    2. Ensure IBKR TWS or Gateway is running
    3. Connect your brokerage to SnapTrade (see instructions below)
    4. Run this script

Author: SnapTrade Integration Team
"""

import os
import sys
from typing import List, Dict, Optional
from datetime import datetime
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
    print("    pip install snaptrade-python-sdk")
    print("\nFull installation command:")
    print("    pip install snaptrade-python-sdk python-dotenv ib_insync")
    sys.exit(1)

# Import IBKR ib_insync
try:
    from ib_insync import IB, Stock, Order, MarketOrder, LimitOrder
except ImportError as e:
    print("❌ ERROR: ib_insync not installed!")
    print("\nPlease install it with:")
    print("    pip install ib_insync")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

# SnapTrade Configuration (loaded from .env)
SNAPTRADE_CLIENT_ID = os.getenv("SNAPTRADE_CLIENT_ID")
SNAPTRADE_CONSUMER_KEY = os.getenv("SNAPTRADE_CONSUMER_KEY")
SNAPTRADE_USER_ID = os.getenv("SNAPTRADE_USER_ID")
SNAPTRADE_USER_SECRET = os.getenv("SNAPTRADE_USER_SECRET")

# IBKR TWS Configuration
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "7497"))  # 7497 for TWS Paper, 7496 for TWS Live
IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "1"))

# Options
DRY_RUN = os.getenv("DRY_RUN", "True").lower() == "true"  # Set to False to place real orders
VERBOSE = os.getenv("VERBOSE", "True").lower() == "true"

# ═══════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

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
        print(f"❌ Missing required environment variables:")
        for var in missing:
            print(f"   - {var}")
        print("\nPlease check your .env file!")
        return False
    
    print("✅ All credentials present")
    if VERBOSE:
        print(f"   Client ID: {SNAPTRADE_CLIENT_ID[:20]}...")
        print(f"   User ID: {SNAPTRADE_USER_ID}")
    return True

# ═══════════════════════════════════════════════════════════════════
# SNAPTRADE CLIENT
# ═══════════════════════════════════════════════════════════════════

class SnapTradeManager:
    """Manages SnapTrade API interactions."""
    
    def __init__(self):
        """Initialize SnapTrade client."""
        self.client = SnapTrade(
            consumer_key=SNAPTRADE_CONSUMER_KEY,
            client_id=SNAPTRADE_CLIENT_ID,
        )
        self.user_id = SNAPTRADE_USER_ID
        self.user_secret = SNAPTRADE_USER_SECRET
    
    def test_connection(self) -> bool:
        """Test API connection."""
        print_section("Testing SnapTrade Connection")
        try:
            response = self.client.api_status.check()
            if VERBOSE:
                print("✅ SnapTrade API Status:")
                pprint(response.body)
            else:
                print("✅ Connected to SnapTrade API")
            return True
        except Exception as e:
            print(f"❌ Failed to connect to SnapTrade: {str(e)}")
            return False
    
    def get_user_accounts(self) -> List[Dict]:
        """
        Fetch all linked brokerage accounts for the user.
        
        Returns:
            List of account dictionaries
        """
        print_section("Fetching User Accounts")
        try:
            response = self.client.account_information.list_user_accounts(
                user_id=self.user_id,
                user_secret=self.user_secret
            )
            
            accounts = response.body if response.body else []
            
            if not accounts:
                print("⚠️  No accounts found!")
                print("\n📝 To link a brokerage account:")
                print("   1. Go to: https://app.snaptrade.com/")
                print("   2. Log in with your SnapTrade account")
                print("   3. Click 'Connect Brokerage'")
                print("   4. Choose your broker (e.g., Interactive Brokers)")
                print("   5. Follow the OAuth flow to authorize")
                print("   6. Run this script again")
                return []
            
            print(f"✅ Found {len(accounts)} account(s):")
            for idx, account in enumerate(accounts, 1):
                print(f"   {idx}. {account.get('name', 'N/A')} (ID: {account.get('id', 'N/A')})")
                print(f"      Type: {account.get('type', 'N/A')}")
                print(f"      Number: {account.get('number', 'N/A')}")
                if account.get('institution_name'):
                    print(f"      Institution: {account.get('institution_name')}")
            
            return accounts
            
        except ApiException as e:
            print(f"❌ API Error fetching accounts: {e}")
            if hasattr(e, 'body'):
                pprint(e.body)
            return []
        except Exception as e:
            print(f"❌ Error fetching accounts: {str(e)}")
            return []
    
    def get_account_orders(self, account_id: str) -> List[Dict]:
        """
        Fetch all orders for a specific account.
        
        Args:
            account_id: The SnapTrade account ID
            
        Returns:
            List of order dictionaries
        """
        print_section(f"Fetching Orders for Account: {account_id}")
        try:
            response = self.client.trading.get_account_orders(
                account_id=account_id,
                user_id=self.user_id,
                user_secret=self.user_secret,
                state="all"  # Can be: all, open, executed, canceled
            )
            
            orders = response.body if response.body else []
            
            if not orders:
                print("   No orders found for this account")
                return []
            
            print(f"✅ Found {len(orders)} order(s):")
            for idx, order in enumerate(orders, 1):
                symbol = order.get('symbol', {})
                print(f"\n   Order {idx}:")
                print(f"      ID: {order.get('id', 'N/A')}")
                print(f"      Symbol: {symbol.get('symbol', 'N/A')}")
                print(f"      Action: {order.get('action', 'N/A')}")
                print(f"      Type: {order.get('order_type', 'N/A')}")
                print(f"      Quantity: {order.get('total_quantity', 'N/A')}")
                print(f"      Status: {order.get('status', 'N/A')}")
                if order.get('limit_price'):
                    print(f"      Limit Price: ${order.get('limit_price')}")
            
            return orders
            
        except ApiException as e:
            print(f"❌ API Error fetching orders: {e}")
            if hasattr(e, 'body'):
                pprint(e.body)
            return []
        except Exception as e:
            print(f"❌ Error fetching orders: {str(e)}")
            return []

# ═══════════════════════════════════════════════════════════════════
# IBKR CLIENT
# ═══════════════════════════════════════════════════════════════════

class IBKRManager:
    """Manages IBKR TWS interactions via ib_insync."""
    
    def __init__(self):
        """Initialize IBKR connection."""
        self.ib = IB()
        self.connected = False
    
    def connect(self) -> bool:
        """Connect to IBKR TWS/Gateway."""
        print_section("Connecting to IBKR TWS")
        try:
            self.ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
            self.connected = True
            print(f"✅ Connected to IBKR at {IBKR_HOST}:{IBKR_PORT}")
            return True
        except Exception as e:
            print(f"❌ Failed to connect to IBKR: {str(e)}")
            print("\n📝 Troubleshooting:")
            print("   1. Ensure IBKR TWS or Gateway is running")
            print("   2. Check that API connections are enabled in TWS:")
            print("      File > Global Configuration > API > Settings")
            print("      ✓ Enable ActiveX and Socket Clients")
            print("   3. Verify the port number:")
            print("      - Paper Trading: 7497")
            print("      - Live Trading: 7496")
            print(f"   4. Current settings: {IBKR_HOST}:{IBKR_PORT}")
            return False
    
    def disconnect(self):
        """Disconnect from IBKR."""
        if self.connected:
            self.ib.disconnect()
            print("   Disconnected from IBKR")
    
    def convert_snaptrade_order_to_ibkr(self, snaptrade_order: Dict) -> Optional[tuple]:
        """
        Convert a SnapTrade order to IBKR format.
        
        Args:
            snaptrade_order: Order dict from SnapTrade
            
        Returns:
            Tuple of (Contract, Order) or None if conversion fails
        """
        try:
            # Extract order details
            symbol_data = snaptrade_order.get('symbol', {})
            symbol = symbol_data.get('symbol', '')
            action = snaptrade_order.get('action', 'BUY').upper()
            quantity = float(snaptrade_order.get('total_quantity', 0))
            order_type = snaptrade_order.get('order_type', 'Market')
            limit_price = snaptrade_order.get('limit_price')
            
            if not symbol or quantity <= 0:
                print(f"   ⚠️  Invalid order data: {symbol} qty={quantity}")
                return None
            
            # Create IBKR Contract (Stock)
            contract = Stock(symbol, 'SMART', 'USD')
            
            # Create IBKR Order
            if order_type.upper() == 'MARKET':
                order = MarketOrder(action, quantity)
            elif order_type.upper() == 'LIMIT':
                if not limit_price:
                    print(f"   ⚠️  Limit order missing price")
                    return None
                order = LimitOrder(action, quantity, limit_price)
            else:
                print(f"   ⚠️  Unsupported order type: {order_type}")
                return None
            
            # Set additional parameters
            order.outsideRth = True  # Allow outside regular trading hours
            
            return (contract, order)
            
        except Exception as e:
            print(f"   ❌ Error converting order: {str(e)}")
            return None
    
    def place_order(self, contract, order) -> bool:
        """
        Place an order in IBKR.
        
        Args:
            contract: IBKR Contract object
            order: IBKR Order object
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if DRY_RUN:
                print(f"   🔵 [DRY RUN] Would place order:")
                print(f"      {order.action} {order.totalQuantity} {contract.symbol}")
                print(f"      Type: {order.orderType}")
                if hasattr(order, 'lmtPrice'):
                    print(f"      Limit Price: ${order.lmtPrice}")
                return True
            
            # Place actual order
            trade = self.ib.placeOrder(contract, order)
            print(f"   ✅ Order placed:")
            print(f"      {order.action} {order.totalQuantity} {contract.symbol}")
            print(f"      Order ID: {trade.order.orderId}")
            
            # Wait a moment for order to be acknowledged
            self.ib.sleep(1)
            
            return True
            
        except Exception as e:
            print(f"   ❌ Failed to place order: {str(e)}")
            return False

# ═══════════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════════════

def main():
    """Main execution flow."""
    print_header("SnapTrade to IBKR Bridge")
    print(f"Mode: {'DRY RUN (No actual orders)' if DRY_RUN else 'LIVE TRADING'}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Step 1: Validate credentials
    if not validate_credentials():
        return 1
    
    # Step 2: Initialize SnapTrade
    print_header("Initializing SnapTrade")
    snaptrade = SnapTradeManager()
    
    if not snaptrade.test_connection():
        return 1
    
    # Step 3: Get user accounts
    accounts = snaptrade.get_user_accounts()
    if not accounts:
        return 1
    
    # Step 4: Get orders from first account
    account_id = accounts[0]['id']
    orders = snaptrade.get_account_orders(account_id)
    
    if not orders:
        print("\n✅ No orders to process")
        return 0
    
    # Filter for open orders only
    open_orders = [o for o in orders if o.get('status', '').upper() in ['OPEN', 'PENDING', 'SUBMITTED']]
    
    if not open_orders:
        print(f"\n   Found {len(orders)} total order(s), but none are open")
        return 0
    
    print(f"\n   Found {len(open_orders)} open order(s) to process")
    
    # Step 5: Connect to IBKR
    print_header("Connecting to IBKR")
    ibkr = IBKRManager()
    
    if not ibkr.connect():
        return 1
    
    try:
        # Step 6: Process each order
        print_header("Processing Orders")
        successful = 0
        failed = 0
        
        for idx, snaptrade_order in enumerate(open_orders, 1):
            print(f"\n[{idx}/{len(open_orders)}] Processing order:")
            
            # Convert SnapTrade order to IBKR format
            result = ibkr.convert_snaptrade_order_to_ibkr(snaptrade_order)
            if not result:
                failed += 1
                continue
            
            contract, order = result
            
            # Place order in IBKR
            if ibkr.place_order(contract, order):
                successful += 1
            else:
                failed += 1
        
        # Step 7: Summary
        print_header("Summary")
        print(f"Total orders processed: {len(open_orders)}")
        print(f"✅ Successful: {successful}")
        print(f"❌ Failed: {failed}")
        print(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE TRADING'}")
        
    finally:
        # Always disconnect from IBKR
        ibkr.disconnect()
    
    return 0

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)