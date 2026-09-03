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


def http_get_json(url, timeout=20, retries=2, headers=None):
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


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def slugify(text, max_len=60):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:max_len].rstrip("-")) or "item"


def strip_html(raw):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw or "")).strip()
