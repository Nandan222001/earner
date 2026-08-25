"""Automated verification of Earner core logic.
Run:  python -m unittest discover -s tests -v
"""
import logging
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.agent import DEFAULTS, deep_merge          # noqa: E402
from core.ledger import Ledger                        # noqa: E402
from strategies.micro_tasks import parse_salary       # noqa: E402
from strategies.trading_bot import TradingBot         # noqa: E402


def make_ctx(db_path):
    ctx = types.SimpleNamespace()
    ctx.ledger = Ledger(db_path=db_path)
    ctx.log = logging.getLogger("test")
    return ctx


def tmp_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.db = tmp_db()
        self.led = Ledger(self.db)

    def tearDown(self):
        self.led.close()
        os.unlink(self.db)

    def test_earn_spend_totals(self):
        self.led.record("t", "earn", 10, note="a")
        self.led.record("t", "spend", 4, note="b")
        self.assertEqual(self.led.day_total(kinds=("earn",)), 10)
        self.assertEqual(self.led.day_total(kinds=("spend",)), 4)

    def test_streak_needs_real_income_at_or_above_target(self):
        self.led.record("t", "lead", 0)
        self.assertEqual(self.led.survival_streak(10), 0)   # leads are not cash
        self.led.record("t", "earn", 9.99)
        self.assertEqual(self.led.survival_streak(10), 0)
        self.led.record("t", "earn", 0.01)
        self.assertEqual(self.led.survival_streak(10), 1)

    def test_paper_results_do_not_count_as_survival(self):
        self.led.record("t", "paper_earn", 500)
        self.assertEqual(self.led.survival_streak(10), 0)
        self.assertEqual(self.led.day_total(kinds=("earn",)), 0)

    def test_state_roundtrip_and_default(self):
        self.assertIsNone(self.led.get_state("missing"))
        self.led.set_state("w", {"usd": 50.5, "position": None})
        self.assertEqual(self.led.get_state("w")["usd"], 50.5)

    def test_invalid_kind_rejected(self):
        with self.assertRaises(AssertionError):
            self.led.record("t", "free_money", 100)


class SalaryParsingTests(unittest.TestCase):
    def test_range_annual(self):
        s = parse_salary("$60,000-80,000 per year")
        self.assertEqual(s["annual"], 80000)
        self.assertAlmostEqual(s["hourly"], 38.46, places=1)

    def test_hourly_rate(self):
        s = parse_salary("pay: $25/hr")
        self.assertEqual(s["hourly"], 25)

    def test_k_suffix(self):
        self.assertEqual(parse_salary("$80k")["annual"], 80000)

    def test_no_salary(self):
        self.assertIsNone(parse_salary("competitive salary"))


class TradingBotTests(unittest.TestCase):
    CFG = {"stake_usd_per_trade": 5, "take_profit_pct": 2,
           "stop_loss_pct": 1.5, "max_daily_loss_usd": 2}
    UPTREND = [100 + i * 0.5 for i in range(40)]     # SMA6 > SMA24 -> buy
    DOWNTREND = [140 - i * 0.7 for i in range(40)]

    def setUp(self):
        self.db = tmp_db()
        self.ctx = make_ctx(self.db)

    def tearDown(self):
        self.ctx.ledger.close()
        os.unlink(self.db)

    def bot_over(self, closes, **extra):
        bot = TradingBot(dict(self.CFG, **extra))
        bot._hourly_closes = lambda symbol: list(closes)
        return bot.run(self.ctx)

    def test_opens_position_on_buy_signal(self):
        res = self.bot_over(self.UPTREND)
        self.assertTrue(res["ok"] and res["mode"] == "paper")
        self.assertIn("BUY", " ".join(res["actions"]))
        w = self.ctx.ledger.get_state("paper_wallet")
        self.assertAlmostEqual(w["usd"], 95.0, places=6)          # 100 - 5 staked
        self.assertAlmostEqual(w["position"]["stake"], 5.0)

    def test_take_profit_closes_with_profit(self):
        slow_rise = [100 + i * 0.1 for i in range(40)]             # entry @ 103.90
        big_jump = [104 + i * 0.3 for i in range(40)]              # exit ~ +11%
        self.bot_over(slow_rise)                                   # opens position
        res = self.bot_over(big_jump)
        self.assertTrue(any(a.startswith("closed") for a in res["actions"]))
        self.assertGreater(self.ctx.ledger.day_total(kinds=("paper_earn",)), 0)
        # bot may legitimately re-enter the same cycle; equity must beat start
        self.assertGreater(res["equity_usd"], 100.0)

    def test_stop_loss_limits_damage(self):
        self.bot_over(self.UPTREND)                                # enter high
        res = self.bot_over(self.DOWNTREND)                        # deep fall
        self.assertTrue(any(a.startswith("closed") for a in res["actions"]))
        self.assertGreater(self.ctx.ledger.day_total(kinds=("spend",)), 0)
        w = self.ctx.ledger.get_state("paper_wallet")
        self.assertLess(w["usd"], 100.0)                           # took the loss
        self.assertIsNone(w["position"])                           # but exited

    def test_daily_loss_cap_blocks_new_entries(self):
        self.ctx.ledger.record("trading_bot", "spend", 3.0)        # pretend lost $3
        res = self.bot_over(self.UPTREND)                          # buy signal...
        self.assertTrue(any("loss cap" in a for a in res["actions"]))
        self.assertIsNone(self.ctx.ledger.get_state("paper_wallet")["position"])

    def test_wait_signal_holds_cash(self):
        res = self.bot_over(self.DOWNTREND)                        # SMA6 < SMA24
        self.assertEqual(res["signal"], "wait")
        self.assertEqual(res["actions"], [])


class ConfigTests(unittest.TestCase):
    def test_deep_merge_preserves_siblings(self):
        out = deep_merge(DEFAULTS, {"survival": {"daily_target_usd": 25},
                                    "strategies": {"trading_bot": {"symbol": "ETHUSDT"}}})
        self.assertEqual(out["survival"]["daily_target_usd"], 25)
        self.assertEqual(out["survival"]["currency"], "USD")     # sibling kept
        self.assertEqual(out["agent"]["name"], "Earner")         # untouched branch kept
        tb = out["strategies"]["trading_bot"]
        self.assertEqual(tb["symbol"], "ETHUSDT")
        self.assertEqual(tb.get("mode", "paper"), "paper")   # runtime default via opt()


if __name__ == "__main__":
    unittest.main(verbosity=2)

