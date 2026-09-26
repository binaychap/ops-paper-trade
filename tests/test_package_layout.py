"""Check package wiring without starting trading workers or contacting brokers."""
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient

from app.main import app
from app.execution.submitter import _load_webull_option_module, _load_webull_stock_module
from app.bearish.executor import BearishPutOptionExecutor


def test_shared_builders_load_from_functional_packages():
    assert _load_webull_stock_module().__name__ == 'app.bullish.stock_bracket'
    assert _load_webull_option_module().__name__ == 'app.options.brackets'
    assert BearishPutOptionExecutor().module is _load_webull_option_module()


def test_ui_assets_remain_accessible():
    # No context manager: lifespan (and trading workers) must not start.
    client = TestClient(app)
    for path in ['/', '/records', '/dashboard.css', '/dashboard.js']:
        assert client.get(path).status_code == 200


def test_bullish_cli_compatibility_from_outside_repository(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, 'DRY_RUN': 'true', 'OPTIONOMICS_POLL_ENABLED': 'false'}
    result = subprocess.run(
        [sys.executable, str(root / 'app/main-top-bullish.py'), '--help'],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=True,
    )
    assert '--once' in result.stdout
    result = subprocess.run(
        [sys.executable, '-m', 'app.bullish.runner', '--help'],
        cwd=root, env=env, capture_output=True, text=True, check=True,
    )
    assert '--once' in result.stdout
