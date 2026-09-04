"""Shared helpers: logging, HTTP with retries, misc."""
import logging
import os
import re
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def setup_logging(level=logging.INFO):
    os.makedirs(DATA_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-18s | %(message)s")
    if not root.handlers:
        fh = RotatingFileHandler(os.path.join(DATA_DIR, "earner.log"),
                                 maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)


def _request(fn, retries):
    last = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


def http_get_json(url, timeout=20, retries=2, headers=None):  # retries overridable
    import requests
    h = {"User-Agent": "EarnerAgent/1.0 (local automation; contact: you)", "Accept": "application/json"}
    if headers:
        h.update(headers)
    return _request(lambda: _raise_ok(requests.get(url, headers=h, timeout=timeout)).json(), retries)


def http_get_text(url, timeout=20, retries=2, headers=None):
    import requests
    h = {"User-Agent": "EarnerAgent/1.0 (local automation; contact: you)"}
    if headers:
        h.update(headers)
    return _request(lambda: _raise_ok(requests.get(url, headers=h, timeout=timeout)).text, retries)


def http_post_json(url, payload=None, auth=None, headers=None, timeout=25, retries=1):
    import requests
    h = {"User-Agent": "EarnerAgent/1.0"}
    if headers:
        h.update(headers)
    return _request(
        lambda: requests.post(url, json=payload or {}, auth=auth, headers=h, timeout=timeout), retries)


def _raise_ok(resp):
    resp.raise_for_status()
    return resp


def post_webhook(url, text):
    """Fire-and-forget Discord/Slack-style webhook notification."""
    if not url:
        return False
    try:
        http_post_json(url, {"content": text, "text": text})
        return True
    except Exception as e:  # noqa: BLE001
        logging.getLogger("notify").warning("webhook failed: %s", e)
        return False


def send_telegram(bot_token, chat_id, text, https_proxy=""):
    """Fire-and-forget Telegram Bot API notification.

    Needs a bot token from @BotFather and the numeric chat/user id of the recipient.
    Optional ``https_proxy`` (e.g. "socks5h://127.0.0.1:1080") routes the call
    through a local SOCKS/HTTP proxy or VPN when api.telegram.org is blocked on the
    network. Left empty, requests falls back to the standard system/env proxy.
    """
    if not bot_token or not chat_id or not text:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        import requests
        proxies = {"https": https_proxy, "http": https_proxy} if https_proxy else None
        resp = requests.post(
            url,
            json={
                "chat_id": str(chat_id).strip(),
                "text": text,
                "disable_web_page_preview": True,
                "disable_notification": False,
            },
            timeout=25,
            proxies=proxies,
        )
        resp.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        logging.getLogger("notify").warning("telegram send failed: %s", e)
        return False


def notify_ntfy(topic, text, base_url="https://ntfy.sh", title=""):
    """Fire-and-forget ntfy.sh push notification.

    Free, no signup, no API key. Choose (or own) a ``topic``, subscribe to it with the
    ntfy Android/iPhone app (or web), and publish there. ``base_url`` can point at a
    self-hosted ntfy instance.
    """
    if not topic or not text:
        return False
    try:
        payload = {"topic": topic, "message": text}
        if title:
            payload["title"] = title
        # JSON publish goes to the server ROOT (topic travels in the body) -
        # posting to /<topic> would make ntfy store the raw JSON as the message.
        http_post_json(f"{base_url.rstrip('/')}/", payload)
        return True
    except Exception as e:  # noqa: BLE001
        logging.getLogger("notify").warning("ntfy send failed: %s", e)
        return False
def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def slugify(text, max_len=60):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:max_len].rstrip("-")) or "item"


def clip(text, n=90):
    """Shorten a string to ~n chars, trimming whole words, for compact notifications."""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if len(t) <= n:
        return t
    cut = t[:n]
    cut = cut[:cut.rfind(" ")] if " " in cut else cut
    return cut.rstrip(" ,.;:") + "…"


def strip_html(raw):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw or "")).strip()


def extract_pitch(markdown):
    """Pull the copy-paste message from a draft (text after the first ---)."""
    text = markdown or ""
    if "\n---\n" in text:
        text = text.split("\n---\n", 1)[1]
    for stop in ("\n---\nPost excerpt:", "\nIssue excerpt:", "\nPost excerpt:"):
        if stop in text:
            text = text.split(stop, 1)[0]
    return text.strip()
