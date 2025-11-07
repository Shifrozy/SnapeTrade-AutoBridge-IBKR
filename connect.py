# save as get_connect_url.py and run:  python get_connect_url.py
import os
from dotenv import load_dotenv
from snaptrade_client import SnapTrade

load_dotenv()
snap = SnapTrade(
    consumer_key=os.environ["SNAPTRADE_CONSUMER_KEY"],
    client_id=os.environ["SNAPTRADE_CLIENT_ID"],
)

link = snap.authentication.get_user_authorization_url(
    user_id=os.environ["SNAPTRADE_USER_ID"],
    broker="ALPACA",          # pick a supported broker; alpaca paper is easiest for testing
    redirect_uri="https://example.com"
).body["authorizationUrl"]

print(link)
