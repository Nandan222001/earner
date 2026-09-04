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
from core.utils import extract_pitch                  # noqa: E402
from core.ledger import Ledger                        # noqa: E402
from strategies.micro_tasks import MicroTasks, parse_salary  # noqa: E402
from strategies.trading_bot import TradingBot         # noqa: E402
from strategies.bounty_scout import BountyScout       # noqa: E402
from strategies.reddit_scout import RedditScout       # noqa: E402


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

    def test_lead_count_is_not_sum_of_zero_amounts(self):
        self.led.record("gigs", "lead", 0, note="a")
        self.led.record("gigs", "lead", 0, note="b")
        self.led.record("gigs", "earn", 12.5)
        today = self.led.per_strategy_today()["gigs"]
        self.assertEqual(today["lead"], 2)
        self.assertEqual(today["earn"], 12.5)

    def test_recent_leads_extracts_url(self):
        self.led.record("reddit_scout", "lead", 0,
                        note="GovStar AI | https://news.ycombinator.com/item?id=1")
        leads = self.led.recent_leads(5)
        self.assertEqual(leads[0]["url"], "https://news.ycombinator.com/item?id=1")
        self.assertIn("GovStar", leads[0]["title"])


class PitchExtractTests(unittest.TestCase):
    def test_strips_header_and_excerpt(self):
        md = "# draft\nLink: x\n\n---\nHi there,\n\nI can start.\n\n---\nPost excerpt:\nnoise"
        self.assertEqual(extract_pitch(md), "Hi there,\n\nI can start.")


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


class JobNormalizeTests(unittest.TestCase):
    def test_json_items_from_common_wrappers(self):
        self.assertEqual(MicroTasks._json_items({"jobs": [1]}), [1])
        self.assertEqual(MicroTasks._json_items({"results": [2]}), [2])
        self.assertEqual(MicroTasks._json_items([3]), [3])

    def test_normalize_nested_company(self):
        j = MicroTasks._normalize_job({
            "id": 9, "title": "Python scraper",
            "company": {"name": "Acme"},
            "contents": "Pay $30/hr remote",
            "refs": {"landing_page": "https://example.com/job"},
        })
        self.assertEqual(j["company"], "Acme")
        self.assertEqual(j["url"], "https://example.com/job")
        self.assertIn("30", j["description"])


class BountyScoutTests(unittest.TestCase):
    def setUp(self):
        self.db = tmp_db()
        self.ctx = make_ctx(self.db)
        self.ctx.notify = lambda t: None
        self.ctx.out_gigs = tempfile.mkdtemp()

    def tearDown(self):
        self.ctx.ledger.close()
        os.unlink(self.db)

    def test_records_leads_from_search(self):
        scout = BountyScout({"skills": ["python"], "max_leads_per_run": 2})
        scout._search = lambda q: [{
            "id": "https://github.com/x/y/issues/1",
            "title": "Pay bounty for docs",
            "repo": "x/y",
            "url": "https://github.com/x/y/issues/1",
            "stars": 0,
            "labels": ["bounty"],
            "score": 18,
            "body": "fix the README",
        }]
        res = scout.run(self.ctx)
        self.assertTrue(res["ok"])
        self.assertEqual(res["new_matches"], 1)
        notes = [t["note"] for t in self.ctx.ledger.recent(5) if t["kind"] == "lead"]
        self.assertTrue(any("Pay bounty for docs" in n for n in notes))
        self.assertGreaterEqual(len(os.listdir(self.ctx.out_gigs)), 1)


class RedditScoutTests(unittest.TestCase):
    def setUp(self):
        self.db = tmp_db()
        self.ctx = make_ctx(self.db)
        self.ctx.notify = lambda t: None
        self.ctx.out_gigs = tempfile.mkdtemp()

    def tearDown(self):
        self.ctx.ledger.close()
        os.unlink(self.db)

    def test_records_hiring_posts(self):
        scout = RedditScout({"max_leads_per_run": 2, "subreddits": ["forhire"],
                             "keywords": ["website", "ai"], "try_reddit": True})
        scout._listing = lambda sub: [{
            "id": "abc123",
            "title": "[Hiring] Need a website + AI chatbot",
            "sub": "forhire",
            "url": "https://www.reddit.com/r/forhire/comments/abc123/",
            "body": "Looking for someone to build a website with AI integration. Budget $400.",
            "author": "client1",
            "comments": 2,
        }]
        scout._search = lambda kws: []
        scout._hn_seeking_freelancer = lambda kws: []
        scout._hn_who_is_hiring = lambda kws: []
        res = scout.run(self.ctx)
        self.assertTrue(res["ok"])
        self.assertEqual(res["new_matches"], 1)
        self.assertGreaterEqual(len(os.listdir(self.ctx.out_gigs)), 1)


class InstagramScoutTests(unittest.TestCase):
    def setUp(self):
        self.db = tmp_db()
        self.ctx = make_ctx(self.db)
        self.ctx.notify = lambda t: None
        self.ctx.out_gigs = tempfile.mkdtemp()
        self.cfg = {"access_token": "tok123", "user_id": "999",
                    "hashtags": ["clinic"],
                    "keywords": ["need", "appointment", "call", "website", "dm"],
                    "max_leads_per_run": 2, "require_contact": True}

    def tearDown(self):
        self.ctx.ledger.close()
        os.unlink(self.db)

    def test_returns_leads_with_captured_contact_and_niche(self):
        from strategies.instagram_scout import InstagramScout
        scout = InstagramScout(self.cfg)
        scout._hashtag_name = lambda tok, uid, h: "ha123"
        scout._recent_media = lambda tok, uid, hid, kw: [{
            "id": "post-1", "username": "dentcare_pune",
            "caption": "New dental clinic in Pune! Call/WhatsApp 98450 12345 for "
                       "appointments, no website yet.",
            "permalink": "instagram.com/p/abc123", "media_type": "IMAGE",
            "like_count": 12, "comments_count": 2,
        }, {
            "id": "post-2", "username": "studio_designs",
            "caption": "We do interiors. Check link in bio.",
            "permalink": "instagram.com/p/xyz", "media_type": "REEL",
            "like_count": 40, "comments_count": 5,
        }]
        res = scout.run(self.ctx)
        self.assertTrue(res["ok"])
        # only the contact-rich lead survives require_contact
        self.assertEqual(len(res["leads"]), 1)
        lead = res["leads"][0]
        self.assertEqual(lead["username"], "dentcare_pune")
        self.assertEqual(lead["phone"], "98450 12345")
        self.assertEqual(lead["niche"], "health")
        self.assertTrue(lead["no_website"])
        self.assertTrue(any("phone=98450 12345" in t["note"]
                            for t in self.ctx.ledger.recent(5) if t["kind"] == "lead"))

    def test_require_contact_false_includes_all(self):
        from strategies.instagram_scout import InstagramScout
        scout = InstagramScout({**self.cfg, "require_contact": False})
        scout._hashtag_name = lambda tok, uid, h: "ha123"
        scout._recent_media = lambda tok, uid, hid, kw: [{
            "id": "post-1", "username": "coffeeshop_nyc",
            "caption": "Just started; need a website, dm us.",   # no phone/email
            "permalink": "instagram.com/p/abc123", "media_type": "IMAGE",
            "like_count": 4, "comments_count": 1,
        }]
        res = scout.run(self.ctx)
        self.assertEqual(res["new_matches"], 1)

    def test_contact_regex_parses_mobile_and_email(self):
        from strategies.instagram_scout import extract_contact
        phone, email = extract_contact("Call us at +91 98450 12345 or mail hello@clinic.in today")
        self.assertEqual(phone, "+91 98450 12345")
        self.assertEqual(email, "hello@clinic.in")
        # URL noise must not be captured as a phone
        p2, _ = extract_contact("see https://instagram.com/p/98450 123456 detail")
        self.assertNotIn("98450", p2 or "")

    def test_skips_when_no_token(self):
        from strategies.instagram_scout import InstagramScout
        scout = InstagramScout({"access_token": ""})
        res = scout.run(self.ctx)
        self.assertFalse(res["ok"])
        self.assertEqual(res.get("skipped"), "no graph token/user_id")

    def test_detect_niche_word_boundaries(self):
        # brand/username substrings must not fake a niche -> wrong offer
        from strategies.instagram_scout import detect_niche
        self.assertEqual(detect_niche("dental clinic, call for appointments"), "health")
        self.assertEqual(detect_niche("relax at our day spa and salon"), "health")
        self.assertEqual(detect_niche("supplies for every dental lab"), "health")
        # 'lab' inside a brand name is NOT health (regression: growthlabs)
        self.assertEqual(detect_niche("AI Labs - add a chatbot to your website"), "business")
        # 'spa' inside 'space' is NOT health
        self.assertEqual(detect_niche("co-working space for startups, contact us"), "business")
        self.assertEqual(detect_niche("new cafe opening, dm to order"), "commerce")
        self.assertEqual(detect_niche("boutique clothing store, link in bio"), "commerce")


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


class TelegramNotifyTests(unittest.TestCase):
    """send_telegram posts the correct payload to the Telegram Bot API (no network)."""

    def test_send_telegram_builds_correct_request(self):
        import core.utils as utils
        import requests
        captured = {}
        orig = requests.post

        def fake_post(url, json=None, timeout=25, proxies=None, **kwargs):
            captured["url"] = url
            captured["json"] = json
            captured["proxies"] = proxies
            resp = types.SimpleNamespace()
            resp.raise_for_status = lambda: None
            return resp

        requests.post = fake_post
        try:
            ok = utils.send_telegram("123456:ABC", "987654321", "hello world")
            self.assertTrue(ok)
            self.assertEqual(captured["url"], "https://api.telegram.org/bot123456:ABC/sendMessage")
            self.assertEqual(captured["json"]["chat_id"], "987654321")
            self.assertEqual(captured["json"]["text"], "hello world")
            self.assertIs(captured["json"]["disable_web_page_preview"], True)
            self.assertIsNone(captured["proxies"])   # no proxy unless configured
        finally:
            requests.post = orig

    def test_send_telegram_uses_proxy_when_configured(self):
        import core.utils as utils
        import requests
        captured = {}
        orig = requests.post

        def fake_post(url, json=None, timeout=25, proxies=None, **kwargs):
            captured["proxies"] = proxies
            resp = types.SimpleNamespace()
            resp.raise_for_status = lambda: None
            return resp

        requests.post = fake_post
        try:
            ok = utils.send_telegram("123456:ABC", "987654321", "hi", "socks5h://127.0.0.1:1080")
            self.assertTrue(ok)
            self.assertEqual(captured["proxies"],
                             {"https": "socks5h://127.0.0.1:1080", "http": "socks5h://127.0.0.1:1080"})
        finally:
            requests.post = orig

    def test_send_telegram_noops_when_unconfigured(self):
        import core.utils as utils
        self.assertFalse(utils.send_telegram("", "", "hi"))
        self.assertFalse(utils.send_telegram("123:TOKEN", "", "hi"))
        self.assertFalse(utils.send_telegram("", "987", "hi"))

    def test_notify_ntfy_builds_correct_request(self):
        import core.utils as utils
        captured = {}
        orig = utils.http_post_json

        def fake_post(url, payload=None, auth=None, headers=None, timeout=25, retries=1):
            captured["url"] = url
            captured["payload"] = payload
            return None

        utils.http_post_json = fake_post
        try:
            ok = utils.notify_ntfy("mysubject", "found a gig", title="Earner")
            self.assertTrue(ok)
            self.assertEqual(captured["url"], "https://ntfy.sh/")
            self.assertEqual(captured["payload"], {"topic": "mysubject", "message": "found a gig", "title": "Earner"})
        finally:
            utils.http_post_json = orig

    def test_notify_ntfy_noops_when_unconfigured(self):
        import core.utils as utils
        self.assertFalse(utils.notify_ntfy("", "hi"))
        self.assertFalse(utils.notify_ntfy("topic", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)

