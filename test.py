
import os
from dotenv import load_dotenv
from snaptrade_client import SnapTrade

load_dotenv()

st = SnapTrade(
    consumer_key=os.environ["SNAPTRADE_CONSUMER_KEY"],
    client_id=os.environ["SNAPTRADE_CLIENT_ID"],
)

resp = st.account_information.list_user_accounts(
    user_id=os.environ["SNAPTRADE_USER_ID"],
    user_secret=os.environ["SNAPTRADE_USER_SECRET"]
)

print("ACCOUNTS:", resp.body)

