from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib import error, request

from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("optionomics_client")
logger.setLevel(logging.INFO)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

OPTIONOMICS_API_URL = os.getenv("OPTIONOMICS_API_URL", "https://optionomics.ai/api/v1/trade_ideas")


def build_headers(user_email: str, api_key: str, *, browser_fallback: bool = False) -> dict[str, str]:
    headers = {
        "X-USER-EMAIL": user_email,
        "X-USER-TOKEN": api_key,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://optionomics.ai/",
    }

    if not browser_fallback:
        headers["Accept"] = "application/json"

    return headers


def fetch_trade_ideas(
    user_email: str,
    api_key: str | None = None,
    *,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    """Fetch trade ideas from the Optionomics API.

    Args:
        user_email: The Optionomics account email.
        api_key: Optional API key. If omitted, uses the OPTIONOMICS_API_KEY env var.
        timeout: HTTP timeout in seconds.

    Returns:
        A list of trade idea payloads as dictionaries.

    Raises:
        RuntimeError: When the API key is missing or the API request fails.
    """
    resolved_key = api_key or os.getenv("OPTIONOMICS_API_KEY")
    if not resolved_key:
        raise RuntimeError("OPTIONOMICS_API_KEY is missing. Set it in the environment or pass api_key=")

    last_error: RuntimeError | None = None
    for browser_fallback in (False, True):
        headers = build_headers(user_email, resolved_key, browser_fallback=browser_fallback)
        logger.info(
            "Requesting Optionomics trade ideas from %s (browser_fallback=%s)",
            OPTIONOMICS_API_URL,
            browser_fallback,
        )
        req = request.Request(OPTIONOMICS_API_URL, headers=headers, method="GET")

        try:
            with request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                #logger.info("Optionomics fetch succeeded for %s (browser_fallback=%s)", OPTIONOMICS_API_URL, browser_fallback)
                break
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"Optionomics API request failed: {exc.code} {body}")
            if exc.code == 403 and not browser_fallback:
                logger.warning("Default headers were blocked by Cloudflare; retrying with browser-like headers.")
                continue
            raise last_error from exc
        except error.URLError as exc:
            last_error = RuntimeError(f"Optionomics API unreachable: {exc.reason}")
            raise last_error from exc

    if last_error is not None:
        raise last_error

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Optionomics response was not valid JSON: {body}") from exc

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("trade_ideas", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return [payload]

    return []


if __name__ == "__main__":
    email = os.getenv("OPTIONOMICS_EMAIL") or "you@example.com"
    api_url = os.getenv("OPTIONOMICS_API_URL", OPTIONOMICS_API_URL)
    try:
        ideas = fetch_trade_ideas(email, timeout=30)
        print(json.dumps(ideas[:3], indent=2))
    except RuntimeError as exc:
        print(f"Error: {exc}")

    print(f"Using Optionomics URL: {api_url}")
