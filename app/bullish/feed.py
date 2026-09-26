"""Fetch bullish Optionomics flow: uv run python app/bullish/feed.py."""

from __future__ import annotations

from app.common.paths import ENV_FILE

import json
import os
import sys
from typing import Any
from urllib import parse

from dotenv import load_dotenv

from app.feeds import optionomics_client


class TopBullish:
    """Read bullish flow using explicit credentials or the project environment."""

    API_URL = "https://optionomics.ai/api/v1/flow/bullish"

    def __init__(
        self,
        user_email: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = 30,
    ) -> None:
        load_dotenv(ENV_FILE)
        self.user_email = user_email or os.getenv("OPTIONOMICS_EMAIL")
        self.api_key = api_key or os.getenv("OPTIONOMICS_API_KEY")
        self.timeout = timeout
        if not self.user_email or not self.api_key:
            raise RuntimeError("Set OPTIONOMICS_EMAIL and OPTIONOMICS_API_KEY or pass credentials.")

    def fetch(self, limit: int = 10) -> Any:
        """Return the API's JSON payload without assuming its response schema."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        url = f"{self.API_URL}?{parse.urlencode({'limit': limit})}"
        return optionomics_client.fetch_json(
            api_url=url,
            user_email=self.user_email,
            api_key=self.api_key,
            timeout=self.timeout,
        )


if __name__ == "__main__":
    try:
        print(json.dumps(TopBullish().fetch(), indent=2))
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
