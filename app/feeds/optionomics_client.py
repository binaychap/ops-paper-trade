from __future__ import annotations

from app.common.paths import ENV_FILE

import json
import logging
import os
from typing import Any
from urllib import error, request

from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("optionomics_client")
logger.setLevel(logging.INFO)

load_dotenv(ENV_FILE)

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


def fetch_json(
    api_url: str,
    user_email: str,
    api_key: str | None = None,
    *,
    timeout: float = 30,
) -> Any:
    """GET a caller-supplied Optionomics URL and return its original JSON payload."""
    resolved_key = api_key or os.getenv("OPTIONOMICS_API_KEY")
    if not resolved_key:
        raise RuntimeError("OPTIONOMICS_API_KEY is missing. Set it in the environment or pass api_key=")

    for browser_fallback in (False, True):
        headers = build_headers(user_email, resolved_key, browser_fallback=browser_fallback)
        logger.info(
            "Requesting Optionomics data from %s (browser_fallback=%s)",
            api_url,
            browser_fallback,
        )
        req = request.Request(api_url, headers=headers, method="GET")

        try:
            with request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                break
        except error.HTTPError as exc:
            last_error = RuntimeError(f"Optionomics API request failed (HTTP {exc.code}).")
            if exc.code == 403 and not browser_fallback:
                logger.warning("Default headers were blocked by Cloudflare; retrying with browser-like headers.")
                continue
            raise last_error from exc
        except (error.URLError, TimeoutError) as exc:
            raise RuntimeError("Optionomics API unreachable or request timed out.") from exc
        except UnicodeDecodeError as exc:
            raise RuntimeError("Optionomics response was not valid UTF-8.") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Optionomics response was not valid JSON.") from exc


def fetch_trade_ideas(
    user_email: str,
    api_key: str | None = None,
    *,
    api_url: str,
    timeout: float = 30,
) -> list[dict[str, Any]]:
    """Fetch from the caller's URL and normalize the trade-ideas response."""
    payload = fetch_json(api_url, user_email, api_key, timeout=timeout)
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
    api_url = os.getenv("OPTIONOMICS_API_URL", "https://optionomics.ai/api/v1/trade_ideas")
    try:
        ideas = fetch_trade_ideas(email, api_url=api_url, timeout=30)
        print(json.dumps(ideas[:3], indent=2))
    except RuntimeError as exc:
        print(f"Error: {exc}")

    print(f"Using Optionomics URL: {api_url}")
