"""Shared sandbox client, account lookup, and order identifiers."""

import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
import sys
from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
# ============================================================
# WEBULL SANDBOX
# ============================================================

_trade_client = None


def get_trade_client():
    global _trade_client

    if _trade_client is not None:
        return _trade_client

    app_key = os.environ["WEBULL_APP_KEY"]
    app_secret = os.environ["WEBULL_APP_SECRET"]

    api_client = ApiClient(
        app_key,
        app_secret,
        "us"
    )

    api_client.add_endpoint(
        "us",
        "api.sandbox.webull.com"
    )

    # Prevent the SDK from creating local file logs in the repository root.
    api_client.set_stream_logger(stream=sys.stdout)

    _trade_client = TradeClient(api_client)
    return _trade_client


# ============================================================
# ACCOUNT
# ============================================================

def get_account_id():
    response = get_trade_client().account_v2.get_account_list()

    if response.status_code != 200:
        raise RuntimeError(
            f"Account lookup failed: "
            f"{response.status_code} {response.text}"
        )

    accounts = response.json()

    if not accounts:
        raise RuntimeError("No Webull account found")

    return accounts[0]["account_id"]


# ============================================================
# UNIQUE ID
# Webull client_order_id max = 32 chars
# ============================================================

def new_id():
    return uuid.uuid4().hex[:32]


