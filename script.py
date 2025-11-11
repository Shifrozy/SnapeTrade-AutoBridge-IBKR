#!/usr/bin/env python3
"""
SnapTrade CSV to IBKR Bridge
=============================
Reads orders from CSV and places them in IBKR via SnapTrade API.
NO TWS/GATEWAY REQUIRED - Uses SnapTrade API directly.

Requirements:
    pip install snaptrade-python-sdk python-dotenv

Setup:
    1. Create a .env file with your credentials (or use defaults in script)
    2. Ensure your IBKR account is connected to SnapTrade
    3. Create orders.csv with your orders
    4. Run this script
"""

import csv
import os
import sys
import time
from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path
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
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

# SnapTrade API Credentials (with defaults from your account)
SNAPTRADE_CLIENT_ID = os.getenv("SNAPTRADE_CLIENT_ID", "EVIEW-TECHNOLOGIES-TEST-UVEVH")
SNAPTRADE_CONSUMER_KEY = os.getenv("SNAPTRADE_CONSUMER_KEY", "jqRizEpeIjBBibDkw6X7rZ0JjIjXt9XwnOmj7ay50gczEbfO5N")
SNAPTRADE_USER_ID = os.getenv("SNAPTRADE_USER_ID", "user_test_2")
SNAPTRADE_USER_SECRET = os.getenv("SNAPTRADE_USER_SECRET", "f1df792c-8338-4a4b-9e6c-4139e455dd79")

# File Paths
CSV_INPUT_FILE = os.getenv("CSV_INPUT_FILE", "orders.csv")

# Options
DRY_RUN = os.getenv("DRY_RUN", "True").lower() == "true"
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
# CSV READER
# ═══════════════════════════════════════════════════════════════════

def read_orders_from_csv(filepath: str) -> List[Dict]:
    """
    Read orders from CSV file.
    Expected columns: Action, Quantity, Symbol, SecType, Exchange, Currency, 
                     TimeInForce, OrderType, LmtPrice, AuxPrice, Account
    """
    print_section(f"Reading Orders from CSV: {filepath}")
    
    csv_path = Path(filepath)
    if not csv_path.exists():
        print(f"❌ CSV file not found: {filepath}")
        print(f"\nCreate a CSV file with columns:")
        print(f"  Action,Quantity,Symbol,SecType,Exchange,Currency,TimeInForce,OrderType,LmtPrice,AuxPrice,Account")
        return []
    
    orders = []
    try:
        with open(csv_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            
            for row_num, row in enumerate(reader, start=2):
                try:
                    # Parse order data
                    order = {
                        'action': row['Action'].strip().upper(),
                        'quantity': float(row['Quantity'].strip()),
                        'symbol': row['Symbol'].strip().upper(),
                        'sec_type': row.get('SecType', 'STK').strip().upper(),
                        'exchange': row.get('Exchange', 'SMART').strip().upper().split('/')[0],
                        'currency': row.get('Currency', 'USD').strip().upper(),
                        'time_in_force': row.get('TimeInForce', 'DAY').strip().upper(),
                        'order_type': row.get('OrderType', 'MKT').strip().upper(),
                        'lmt_price': float(row['LmtPrice'].strip()) if row.get('LmtPrice', '').strip() else None,
                        'aux_price': float(row['AuxPrice'].strip()) if row.get('AuxPrice', '').strip() else None,
                        'account': row.get('Account', '').strip(),
                        'row_number': row_num
                    }
                    
                    # Validate required fields
                    if not order['symbol'] or order['quantity'] <= 0:
                        print(f"   ⚠️  Row {row_num}: Invalid data, skipping")
                        continue
                    
                    if order['action'] not in ['BUY', 'SELL']:
                        print(f"   ⚠️  Row {row_num}: Invalid action '{order['action']}', skipping")
                        continue
                    
                    orders.append(order)
                    print(f"   ✅ Row {row_num}: {order['action']} {order['quantity']} {order['symbol']}")
                    
                except (ValueError, KeyError) as e:
                    print(f"   ⚠️  Row {row_num}: Error parsing - {str(e)}")
                    continue
        
        print(f"\n✅ Loaded {len(orders)} valid order(s) from CSV")
        return orders
        
    except Exception as e:
        print(f"❌ Error reading CSV: {str(e)}")
        return []

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
        """Fetch all linked brokerage accounts for the user."""
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
    
    def search_symbol(self, symbol: str, account_id: str) -> Optional[str]:
        """Search for a symbol and return its universal_symbol_id."""
        try:
            # Use symbol search endpoint (works on FREE plan)
            response = self.client.reference_data.symbol_search_user_account(
                user_id=self.user_id,
                user_secret=self.user_secret,
                account_id=account_id,
                substring=symbol
            )
            
            if response.body and len(response.body) > 0:
                # Find exact match or first result
                for sym in response.body:
                    symbol_name = sym.get('symbol', '').upper()
                    if symbol_name == symbol.upper():
                        symbol_id = sym.get('id')
                        if symbol_id:
                            if VERBOSE:
                                print(f"      Symbol {symbol} → ID: {symbol_id}")
                            return symbol_id
                
                # Use first result if no exact match
                symbol_id = response.body[0].get('id')
                if symbol_id:
                    if VERBOSE:
                        print(f"      Symbol {symbol} → ID: {symbol_id} (first match)")
                    return symbol_id
                else:
                    print(f"      ⚠️  Symbol ID not found for: {symbol}")
                    return None
            else:
                print(f"      ⚠️  Symbol not found: {symbol}")
                return None
                
        except Exception as e:
            print(f"      ❌ Error searching symbol {symbol}: {str(e)}")
            return None
    
    def place_order_from_csv(self, account_id: str, csv_order: Dict) -> bool:
        """Place an order from CSV data using SnapTrade API."""
        try:
            symbol = csv_order['symbol']
            action = csv_order['action']
            quantity = csv_order['quantity']
            order_type = csv_order['order_type']
            lmt_price = csv_order.get('lmt_price')
            tif = csv_order.get('time_in_force', 'Day')
            
            print(f"      Searching for symbol: {symbol}")
            
            # Search for symbol to get universal_symbol_id
            symbol_id = self.search_symbol(symbol, account_id)
            if not symbol_id:
                return False
            
            # Map order type
            order_type_map = {
                'MKT': 'Market',
                'LMT': 'Limit',
                'STP': 'Stop',
                'MARKET': 'Market',
                'LIMIT': 'Limit',
                'STOP': 'Stop'
            }
            snaptrade_order_type = order_type_map.get(order_type, 'Market')
            
            # Map time in force
            tif_map = {
                'DAY': 'Day',
                'GTC': 'GTC',
                'IOC': 'IOC',
                'FOK': 'FOK'
            }
            snaptrade_tif = tif_map.get(tif, 'Day')
            
            if DRY_RUN:
                print(f"      🔵 [DRY RUN] Would place order via SnapTrade:")
                print(f"         {action} {quantity} {symbol}")
                print(f"         Type: {snaptrade_order_type} | TIF: {snaptrade_tif}")
                if lmt_price:
                    print(f"         Limit Price: ${lmt_price}")
                return True
            
            # Prepare order payload for SnapTrade
            order_data = {
                'account_id': account_id,
                'action': action.capitalize(),  # Buy or Sell
                'order_type': snaptrade_order_type,
                'time_in_force': snaptrade_tif,
                'universal_symbol_id': symbol_id,
                'units': int(quantity)
            }
            
            # Add price if limit order
            if snaptrade_order_type == 'Limit' and lmt_price:
                order_data['price'] = float(lmt_price)
            
            print(f"      📤 Placing order via SnapTrade...")
            
            # Place order through SnapTrade
            response = self.client.trading.place_force_order(
                user_id=self.user_id,
                user_secret=self.user_secret,
                **order_data
            )
            
            if response.body:
                order_result = response.body
                print(f"      ✅ Order placed successfully!")
                print(f"         Order ID: {order_result.get('id', 'N/A')}")
                print(f"         Status: {order_result.get('status', 'N/A')}")
                return True
            else:
                print(f"      ❌ Order placement failed")
                return False
                
        except ApiException as e:
            print(f"      ❌ API Error: {e}")
            if hasattr(e, 'body'):
                pprint(e.body)
            return False
        except Exception as e:
            print(f"      ❌ Error placing order: {str(e)}")
            return False

# ═══════════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════════════

def main():
    """Main execution flow."""
    print_header("CSV to IBKR via SnapTrade API")
    print(f"Mode: {'DRY RUN (No actual orders)' if DRY_RUN else 'LIVE TRADING'}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n💡 Orders will be placed through SnapTrade API")
    print("   No need to run TWS/Gateway!")
    
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
    
    account_id = accounts[0]['id']
    account_name = accounts[0].get('name', 'N/A')
    print(f"\n   Using account: {account_name}")
    
    # Step 4: Read orders from CSV
    orders = read_orders_from_csv(CSV_INPUT_FILE)
    
    if not orders:
        print("\n❌ No valid orders found in CSV file")
        return 1
    
    # Step 5: Process each order through SnapTrade
    print_header("Processing Orders via SnapTrade API")
    successful = 0
    failed = 0
    
    for idx, csv_order in enumerate(orders, 1):
        print(f"\n[{idx}/{len(orders)}] Processing order from row {csv_order['row_number']}:")
        print(f"   {csv_order['action']} {csv_order['quantity']} {csv_order['symbol']}")
        print(f"   Type: {csv_order['order_type']} | TIF: {csv_order['time_in_force']}")
        
        # Place order via SnapTrade
        if snaptrade.place_order_from_csv(account_id, csv_order):
            successful += 1
        else:
            failed += 1
        
        # Small delay between orders
        if idx < len(orders):
            time.sleep(1)
    
    # Step 6: Summary
    print_header("Summary")
    print(f"Total orders processed: {len(orders)}")
    print(f"✅ Successful: {successful}")
    print(f"❌ Failed: {failed}")
    print(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE TRADING'}")
    print(f"\n💡 Orders placed via SnapTrade API → Your connected IBKR account")
    
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