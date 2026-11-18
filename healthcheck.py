"""End-to-end readiness check for the Fintrack container.

This is intentionally separate from bot.py so Docker can verify dependencies
without starting a second polling process. It prints only status, latency and
exception type; secrets and request URLs are never included in the output.
"""

import json
import os
import sys
import time

import httpx

from sheets import TRANSACTION_SHEET_NAME, _get_sheet


def _result(ok: bool, elapsed: float, error_type: str | None = None, status: int | None = None):
    value = {"ok": ok, "latency_ms": round(elapsed * 1000)}
    if status is not None:
        value["status"] = status
    if error_type is not None:
        value["error_type"] = error_type
    return value


def check_telegram(timeout: float) -> dict:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return {"ok": False, "error_type": "MissingToken"}

    started = time.monotonic()
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(
                f"https://api.telegram.org/bot{token}/getMe",
            )
        payload = response.json()
        ok = response.status_code == 200 and payload.get("ok") is True
        return _result(ok, time.monotonic() - started, status=response.status_code)
    except Exception as exc:
        return _result(False, time.monotonic() - started, type(exc).__name__)


def check_sheets() -> dict:
    started = time.monotonic()
    try:
        sheet = _get_sheet(TRANSACTION_SHEET_NAME)
        sheet.acell("A1").value
        return _result(True, time.monotonic() - started)
    except Exception as exc:
        return _result(False, time.monotonic() - started, type(exc).__name__)


def main() -> int:
    timeout = float(os.environ.get("FINTRACK_HEALTHCHECK_TIMEOUT", "8"))
    result = {
        "telegram": check_telegram(timeout),
        "sheets": check_sheets(),
    }
    result["ok"] = result["telegram"]["ok"] and result["sheets"]["ok"]
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
