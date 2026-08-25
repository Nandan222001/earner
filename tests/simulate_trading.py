"""Prove the paper-trading engine behaves sanely across hundreds of cycles.
Run:  python tests\\simulate_trading.py
"""
import logging
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.ledger import Ledger            # noqa: E402
from strategies.trading_bot import TradingBot  # noqa: E402

logging.disable(logging.CRITICAL)

# Synthetic market: two trend legs + oscillation, 400 hourly closes.
prices, p = [], 100.0
for i in range(400):
    wave = 6 * __import__("math").sin(i / 12.0)
    trend = 20 if i < 200 else -18          # rally then slide back
    prices.append(round(p + trend * (i % 200) / 200 + wave, 2))

f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
f.close()
ctx = types.SimpleNamespace()
ctx.ledger = Ledger(f.name)
ctx.log = logging.getLogger("sim")

bot = TradingBot({"stake_usd_per_trade": 10, "take_profit_pct": 2,
                  "stop_loss_pct": 1.5, "max_daily_loss_usd": 9999})

events = 0
for i in range(30, len(prices)):
    window = prices[:i + 1]
    bot._hourly_closes = lambda s, w=window: list(w)   # feed history so far
    res = bot.run(ctx)
    if res.get("actions"):
        events += 1
        print(f"cycle {i:>3}  px={res['price']:>8.2f}  "
              f"{' | '.join(res['actions'])}  "
              f"[cash ${res['wallet_usd']:.2f}]")

w = ctx.ledger.get_state("paper_wallet")
wins = ctx.ledger.day_total(kinds=("paper_earn",))
losses = ctx.ledger.day_total(kinds=("spend",))
pos = w.get("position")

print("-" * 62)
print(f"decision events      : {events}")
print(f"closed trades        : winners ${wins:.2f} | losers ${losses:.2f} "
      f"| net {wins - losses:+.2f}")
print(f"final cash           : ${w['usd']:.2f}"
      + (f"  (open position entry {pos['entry']})" if pos else ""))
print("PASSED" if events > 0 and w["usd"] > 0 else "FAILED: no activity?")
ctx.ledger.close()
os.unlink(f.name)
