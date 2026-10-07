"""Run Telegram's local Bot API server and the bot in one Render worker."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP = Path("/app")
STATE = APP / "state"
BOT_DIR = APP / "WEBSITE_DEVELOPER_SOURCE"
API_URL = "http://127.0.0.1:8081"
children = []


def stop_children(signum=None, frame=None):
    for process in reversed(children):
        if process.poll() is None:
            process.terminate()
    for process in reversed(children):
        if process.poll() is None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    if signum is not None:
        sys.exit(128 + signum)


def request_json(url, timeout=15):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def main():
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token or not os.environ.get("TELEGRAM_API_ID") or not os.environ.get("TELEGRAM_API_HASH"):
        raise RuntimeError("BOT_TOKEN, TELEGRAM_API_ID and TELEGRAM_API_HASH are required")

    for path in (STATE / "auth", STATE / "telegram", APP / "tmp/telegram-files", APP / "tmp/telegram-temp"):
        path.mkdir(parents=True, exist_ok=True)

    auth = BOT_DIR / "auth"
    if not auth.is_symlink() and not auth.exists():
        auth.symlink_to(STATE / "auth", target_is_directory=True)
    if auth.resolve() != (STATE / "auth").resolve():
        raise RuntimeError("Bot auth directory is not on the persistent disk")

    migration_marker = STATE / ".hosted-api-logged-out"
    if not migration_marker.exists():
        # Telegram requires logOut before a bot can reliably receive updates locally.
        try:
            result = request_json(f"https://api.telegram.org/bot{token}/logOut")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("Could not deregister the bot from Telegram's hosted API") from exc
        if not result.get("ok"):
            raise RuntimeError("Telegram's hosted API rejected logOut")
        migration_marker.touch()

    api = subprocess.Popen([
        "telegram-bot-api", "--local", "--http-ip-address=127.0.0.1",
        f"--dir={STATE / 'telegram'}",
        f"--files-dir={APP / 'tmp/telegram-files'}",
        f"--temp-dir={APP / 'tmp/telegram-temp'}",
    ])
    children.append(api)

    for _ in range(90):
        if api.poll() is not None:
            raise RuntimeError("Telegram local Bot API server exited during startup")
        try:
            if request_json(f"{API_URL}/bot{token}/getMe", timeout=2).get("ok"):
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass
        time.sleep(2)
    else:
        raise RuntimeError("Telegram local Bot API server did not become ready")

    os.environ["LOCAL_BOT_API_URL"] = API_URL
    print("Telegram local Bot API ready; starting bot", flush=True)
    bot = subprocess.Popen([sys.executable, str(BOT_DIR / "bot.py")], cwd=BOT_DIR)
    children.append(bot)
    while api.poll() is None and bot.poll() is None:
        time.sleep(2)
    if api.poll() is not None:
        raise RuntimeError("Telegram local Bot API server stopped")
    return bot.returncode


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop_children)
    signal.signal(signal.SIGINT, stop_children)
    try:
        sys.exit(main())
    finally:
        stop_children()
