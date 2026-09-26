import io
import json
from urllib.error import HTTPError

from app.feeds import optionomics_client as client


def test_caller_url_and_original_payload(monkeypatch):
    payload = {"data": [{"symbol": "TEST"}], "total": 1}
    calls = []

    def urlopen(req, timeout):
        calls.append((req.full_url, req.get_header("X-user-token"), timeout))
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(client.request, "urlopen", urlopen)
    url = "https://optionomics.ai/api/v1/flow/bullish?limit=10"
    assert client.fetch_json(url, "test@example.com", "test-key", timeout=5) == payload
    assert calls == [(url, "test-key", 5)]
    assert client.fetch_trade_ideas("test@example.com", "test-key", api_url=url) == payload["data"]


def test_successful_retry_does_not_raise_previous_error(monkeypatch):
    calls = []

    def urlopen(req, timeout):
        calls.append(req)
        if len(calls) == 1:
            raise HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b"blocked"))
        return io.BytesIO(b'[{"symbol": "TEST"}]')

    monkeypatch.setattr(client.request, "urlopen", urlopen)
    assert client.fetch_json("https://optionomics.ai/api/v1/flow/bullish", "test@example.com", "test-key") == [{"symbol": "TEST"}]
    assert len(calls) == 2
