"""Agent orchestrator: loads config+strategies, runs cycles, reports status."""
import copy
import json
import logging
import os
import time
import traceback

import yaml

from .ledger import Ledger
from .utils import DATA_DIR, post_webhook, today_str

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "agent": {"name": "Earner", "loop_interval_minutes": 30},
    "survival": {"daily_target_usd": 10.0, "currency": "USD"},
    "notifications": {"webhook_url": ""},
    "strategies": {},
}


def deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class AgentContext:
    """Passed to every strategy.run() call."""

    def __init__(self, config, ledger, log):
        self.config = config
        self.ledger = ledger
        self.log = log
        self.out_articles = os.path.join(ROOT, "data", "output", "articles")
        self.out_gigs = os.path.join(ROOT, "data", "output", "gigs")
        os.makedirs(self.out_articles, exist_ok=True)
        os.makedirs(self.out_gigs, exist_ok=True)

    @property
    def webhook(self):
        return (self.config.get("notifications") or {}).get("webhook_url", "")

    def notify(self, text):
        self.log.info("NOTIFY: %s", text.replace("\n", " | "))
        post_webhook(self.webhook, text)


class Agent:
    def __init__(self, config_path="config.yaml"):
        from strategies import build_enabled  # late import avoids cycle
        path = config_path if os.path.isabs(config_path) else os.path.join(ROOT, config_path)
        user_cfg = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                user_cfg = yaml.safe_load(f) or {}
        else:
            user_cfg = {}
        self.config = deep_merge(DEFAULTS, user_cfg)

        logging.getLogger("earner").info("config loaded (%s)", os.path.basename(path))
        self.log = logging.getLogger("earner")
        self.ledger = Ledger()
        self.ctx = AgentContext(self.config, self.ledger, self.log)
        self.strategies = build_enabled(self.config)
        names = [s.name for s in self.strategies] or ["none"]
        self.log.info("active strategies: %s", ", ".join(names))

    # ------------------------------------------------------------------
    def run_once(self):
        results = {}
        for strat in self.strategies:
            t0 = time.time()
            self.log.info("[%s] running...", strat.name)
            try:
                res = strat.run(self.ctx) or {}
                res.setdefault("ok", True)
                results[strat.name] = res
                self.log.info("[%s] OK in %.1fs -> %s", strat.name, time.time() - t0,
                              json.dumps(res, default=str)[:300])
            except Exception as e:  # noqa: BLE001 - isolate strategy failures
                results[strat.name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                self.log.error("[%s] FAILED: %s\n%s", strat.name, e, traceback.format_exc())
        self.write_status(results)
        return results

    def run_forever(self, interval_minutes=None):
        interval = float(interval_minutes or self.config["agent"]["loop_interval_minutes"])
        self.log.info("loop started: every %.0f min. Ctrl+C to stop.", interval)
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001
                self.log.error("cycle crashed (continuing): %s", e)
            try:
                time.sleep(interval * 60)
            except KeyboardInterrupt:
                self.log.info("stopped by user")
                return

    # ------------------------------------------------------------------
    def status_payload(self, strategy_results=None):
        cfg_surv = self.config["survival"]
        target = float(cfg_surv["daily_target_usd"])
        real_today = self.ledger.day_total(kinds=("earn",))
        paper_today = self.ledger.day_total(kinds=("paper_earn", "spend"))
        wallet = self.ledger.get_state("paper_wallet", {"usd": 100.0, "position": None})
        progress = round(min(real_today / target * 100, 999), 1) if target > 0 else 0
        arts_dir = self.ctx.out_articles
        gigs_dir = self.ctx.out_gigs
        return {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "agent": self.config["agent"]["name"],
            "day": today_str(),
            "survival": {
                "target_usd": target,
                "real_earned_today": real_today,
                "paper_pnl_today": paper_today,
                "progress_pct": progress,
                "alive": real_today >= target,
                "streak_days": self.ledger.survival_streak(target),
            },
            "paper_wallet": wallet,
            "per_strategy_today": self.ledger.per_strategy_today(),
            "strategies": strategy_results or {},
            "recent_transactions": self.ledger.recent(12),
            "articles": sorted(os.listdir(arts_dir))[-10:] if os.path.isdir(arts_dir) else [],
            "gigs": sorted(os.listdir(gigs_dir))[-10:] if os.path.isdir(gigs_dir) else [],
        }

    def write_status(self, strategy_results=None):
        payload = self.status_payload(strategy_results)
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "status.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
        return payload
