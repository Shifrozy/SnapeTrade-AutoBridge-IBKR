# SnapTrade-AutoBridge-IBKR

> **Automated CSV-to-IBKR Order Execution via SnapTrade API**

A Python script that reads trade orders from a CSV file and automatically places them in your Interactive Brokers account using the SnapTrade API. No need to run TWS/Gateway!

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![SnapTrade](https://img.shields.io/badge/SnapTrade-API-orange)](https://snaptrade.com)

---

## 🌟 Features

- ✅ **CSV-based Order Management** - Define orders in a simple CSV file
- ✅ **SnapTrade API Integration** - Direct connection to IBKR via SnapTrade (no TWS required)
- ✅ **Automatic Symbol Lookup** - Converts ticker symbols to SnapTrade universal IDs
- ✅ **Dry Run Mode** - Test orders without actual execution
- ✅ **Multiple Order Types** - Supports Market, Limit, and Stop orders
- ✅ **Paper Trading Support** - Works with IBKR demo accounts
- ✅ **Detailed Logging** - Track every order with timestamps
- ✅ **Error Handling** - Graceful handling of API failures

---

## 📋 Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [CSV Format](#csv-format)
- [Usage](#usage)
- [How It Works](#how-it-works)
- [Modes](#modes)
- [Troubleshooting](#troubleshooting)
- [API Reference](#api-reference)
- [Contributing](#contributing)
- [License](#license)

---

## 🔧 Prerequisites

Before you begin, ensure you have:

1. **Python 3.10+** installed
2. **SnapTrade Account** - Sign up at [app.snaptrade.com](https://app.snaptrade.com/)
3. **SnapTrade API Credentials** - Get from SnapTrade dashboard
4. **IBKR Account** - Connected to SnapTrade (Paper or Live)

---

## 📦 Installation

### 1. Clone the Repository

```bash
git clone https://github.com/Shifrozy/SnapeTrade-AutoBridge-IBKR.git
cd SnapeTrade-AutoBridge-IBKR
```

### 2. Install Dependencies

```bash
pip install snaptrade-python-sdk python-dotenv
```

Or use requirements.txt:

```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```properties
# SnapTrade API Credentials
SNAPTRADE_CLIENT_ID=your_client_id_here
SNAPTRADE_CONSUMER_KEY=your_consumer_key_here
SNAPTRADE_USER_ID=your_user_id_here
SNAPTRADE_USER_SECRET=your_user_secret_here

# Options
DRY_RUN=True          # Set to False for live trading
VERBOSE=True          # Detailed logging
CSV_INPUT_FILE=orders.csv
```

---

## ⚙️ Configuration

### Getting SnapTrade Credentials

1. **Sign up** at [app.snaptrade.com](https://app.snaptrade.com/)
2. **Get API Keys** from Dashboard → Settings → API
   - `SNAPTRADE_CLIENT_ID` (Consumer Key)
   - `SNAPTRADE_CONSUMER_KEY` (Consumer Secret)
3. **Register a User** (first time only):
   ```python
   # Your user_id can be any unique string
   SNAPTRADE_USER_ID = "your_unique_user_id"
   ```
4. **Get User Secret** - Returned when you register (save it!)

### Connecting Your IBKR Account

1. Go to [app.snaptrade.com](https://app.snaptrade.com/)
2. Click **"Connect Brokerage"**
3. Select **Interactive Brokers**
4. Complete OAuth authorization
5. Your account will appear in the dashboard

---

## 📄 CSV Format

Create an `orders.csv` file with the following columns:

```csv
Action,Quantity,Symbol,SecType,Exchange,Currency,TimeInForce,OrderType,LmtPrice,AuxPrice,Account
BUY,10,AAPL,STK,SMART,USD,DAY,MKT,,,DU1234567
SELL,5,TSLA,STK,SMART,USD,GTC,LMT,250.50,,DU1234567
BUY,100,SPY,STK,SMART,USD,DAY,MKT,,,DU1234567
```

### Column Descriptions

| Column | Required | Description | Example |
|--------|----------|-------------|---------|
| `Action` | ✅ | BUY or SELL | `BUY` |
| `Quantity` | ✅ | Number of shares | `10` |
| `Symbol` | ✅ | Stock ticker | `AAPL` |
| `SecType` | ❌ | Security type | `STK` (default) |
| `Exchange` | ❌ | Exchange | `SMART` (default) |
| `Currency` | ❌ | Currency | `USD` (default) |
| `TimeInForce` | ❌ | Order duration | `DAY`, `GTC`, `IOC`, `FOK` |
| `OrderType` | ❌ | Order type | `MKT`, `LMT`, `STP` |
| `LmtPrice` | ❌* | Limit price | `150.50` |
| `AuxPrice` | ❌* | Stop price | `145.00` |
| `Account` | ❌ | IBKR account number | `DU1234567` |

*Required for Limit/Stop orders

---

## 🚀 Usage

### Basic Usage (Dry Run)

```bash
python script.py
```

This will:
1. Read orders from `orders.csv`
2. Connect to SnapTrade API
3. Validate symbols
4. Simulate order placement (no actual orders)

### Live Trading (Paper Account)

Set in `.env`:
```properties
DRY_RUN=False
```

Then run:
```bash
python script.py
```

### Expected Output

```
======================================================================
  CSV to IBKR via SnapTrade API
======================================================================
Mode: DRY RUN (No actual orders)
Time: 2025-11-12 00:27:37

--- Reading Orders from CSV: orders.csv ---
   ✅ Row 2: BUY 1.0 AAPL
   ✅ Row 3: SELL 1.0 IEF

✅ Loaded 2 valid order(s) from CSV

--- Processing Orders via SnapTrade API ---
[1/2] Processing order from row 2:
   BUY 1.0 AAPL
   Type: MKT | TIF: DAY
      Searching for symbol: AAPL
      Symbol AAPL → ID: c15a817e-7171-4940-9ae7-f7b4a95408ee
      🔵 [DRY RUN] Would place order via SnapTrade:
         BUY 1.0 AAPL
         Type: Market | TIF: Day

======================================================================
  Summary
======================================================================
Total orders processed: 2
✅ Successful: 2
❌ Failed: 0
Mode: DRY RUN
```

---

## 🔄 How It Works

```
┌─────────────┐
│  orders.csv │
└──────┬──────┘
       │
       ↓
┌─────────────────┐
│  Python Script  │
│  - Read CSV     │
│  - Validate     │
└────────┬────────┘
         │
         ↓
┌──────────────────┐
│  SnapTrade API   │
│  - Auth          │
│  - Symbol Lookup │
│  - Place Order   │
└────────┬─────────┘
         │
         ↓
┌──────────────────┐
│  IBKR Account    │
│  (Paper/Live)    │
└──────────────────┘
```

### Step-by-Step Process

1. **CSV Parsing** - Reads and validates order data
2. **SnapTrade Connection** - Authenticates with API credentials
3. **Account Selection** - Fetches your connected IBKR accounts
4. **Symbol Resolution** - Converts tickers (AAPL) to universal symbol IDs
5. **Order Placement** - Submits orders via SnapTrade API
6. **Confirmation** - Returns order IDs and status

---

## 🎯 Modes

### Dry Run Mode (Default)
- **Setting**: `DRY_RUN=True`
- **Behavior**: Simulates orders without actual execution
- **Use Case**: Testing, validation, debugging
- **Safe**: No real money involved

### Live Trading Mode
- **Setting**: `DRY_RUN=False`
- **Behavior**: Places actual orders in IBKR
- **Use Case**: Production trading
- **Risk**: Real money (use carefully!)

### Paper Trading (Recommended for Testing)
- **Account Type**: IBKR Paper Trading account (starts with "DU")
- **Behavior**: Real orders, virtual money
- **Use Case**: Safe testing environment
- **Risk**: None (demo money only)

---

## 🛠️ Troubleshooting

### Common Issues

#### 1. ModuleNotFoundError: No module named 'snaptrade_client'

**Solution:**
```bash
pip install snaptrade-python-sdk
```

**Note:** Import as `snaptrade_client`, not `snaptrade`!

---

#### 2. No Accounts Found

**Error:**
```
⚠️ No accounts found!
```

**Solution:**
1. Go to [app.snaptrade.com](https://app.snaptrade.com/)
2. Connect your IBKR account via OAuth
3. Wait 1-2 minutes for sync
4. Run script again

---

#### 3. Symbol Not Found

**Error:**
```
⚠️ Symbol not found: XYZ
```

**Solution:**
- Verify ticker symbol is correct
- Check if symbol is available on your exchange
- Some symbols may not be supported

---

#### 4. 403 Forbidden - Market Quotes Disabled

**Error:**
```
Market quotes are disabled for FREE plan
```

**Solution:**
- This script uses `symbol_search_user_account` (FREE plan compatible)
- Ensure you're using the latest version
- If issue persists, upgrade to SnapTrade paid plan

---

#### 5. Invalid Credentials

**Error:**
```
401 Unauthorized - Invalid userId or userSecret
```

**Solution:**
1. Verify credentials in `.env` file
2. Check for typos or extra spaces
3. Ensure user is registered in SnapTrade
4. Generate new credentials if needed

---

## 📚 API Reference

### SnapTrade SDK Methods Used

```python
# Check API status
client.api_status.check()

# Get user accounts
client.account_information.list_user_accounts(
    user_id=user_id,
    user_secret=user_secret
)

# Search symbols
client.reference_data.symbol_search_user_account(
    user_id=user_id,
    user_secret=user_secret,
    account_id=account_id,
    substring=symbol
)

# Place order
client.trading.place_force_order(
    user_id=user_id,
    user_secret=user_secret,
    account_id=account_id,
    action="Buy",
    order_type="Market",
    time_in_force="Day",
    universal_symbol_id=symbol_id,
    units=quantity
)
```

---

## 📊 Example CSV Files

### Market Orders
```csv
Action,Quantity,Symbol,SecType,Exchange,Currency,TimeInForce,OrderType,LmtPrice,AuxPrice,Account
BUY,100,AAPL,STK,SMART,USD,DAY,MKT,,,DU1234567
BUY,50,MSFT,STK,SMART,USD,DAY,MKT,,,DU1234567
SELL,75,TSLA,STK,SMART,USD,DAY,MKT,,,DU1234567
```

### Limit Orders
```csv
Action,Quantity,Symbol,SecType,Exchange,Currency,TimeInForce,OrderType,LmtPrice,AuxPrice,Account
BUY,10,AAPL,STK,SMART,USD,GTC,LMT,150.50,,DU1234567
SELL,5,GOOGL,STK,SMART,USD,DAY,LMT,2800.00,,DU1234567
```

### Mixed Orders
```csv
Action,Quantity,Symbol,SecType,Exchange,Currency,TimeInForce,OrderType,LmtPrice,AuxPrice,Account
BUY,100,SPY,STK,SMART,USD,DAY,MKT,,,DU1234567
BUY,50,QQQ,STK,SMART,USD,GTC,LMT,380.00,,DU1234567
SELL,25,IWM,STK,SMART,USD,DAY,MKT,,,DU1234567
```

---

## 🔐 Security Best Practices

1. **Never commit `.env` file** to Git
2. **Keep API keys secret** - Don't share publicly
3. **Use paper trading** for testing
4. **Enable 2FA** on SnapTrade account
5. **Rotate credentials** periodically
6. **Monitor API usage** - Check rate limits
7. **Review orders** before executing

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

### Development Setup

```bash
# Clone repo
git clone https://github.com/Shifrozy/SnapeTrade-AutoBridge-IBKR.git

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests
pytest tests/
```

### Contribution Guidelines

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- [SnapTrade](https://snaptrade.com/) - For providing the brokerage integration API
- [Interactive Brokers](https://www.interactivebrokers.com/) - Trading platform
- Python community for excellent libraries

---

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/yourusername/SnapeTrade-AutoBridge-IBKR/issues)
- **SnapTrade Docs**: [docs.snaptrade.com](https://docs.snaptrade.com/)
- **IBKR API**: [interactivebrokers.github.io](https://interactivebrokers.github.io/tws-api/)

---

## ⚠️ Disclaimer

This software is for educational purposes only. Trading involves risk. Always test with paper trading first. The authors are not responsible for any financial losses incurred from using this software.

---

## 📈 Roadmap

- [ ] Web UI for order management
- [ ] Portfolio tracking
- [ ] Multiple account support
- [ ] Advanced order types (brackets, OCO)
- [ ] Real-time order status monitoring
- [ ] Email/SMS notifications
- [ ] Performance analytics
- [ ] Risk management rules

---

## 🌟 Star History

If you find this project useful, please consider giving it a star! ⭐

---

**Made with ❤️ by Hassan**

*Happy Trading! 🚀*