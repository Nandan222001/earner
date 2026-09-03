"""Earner CLI.

Usage:
  python earner.py run --once          run all strategies once
  python earner.py run                 run forever on a schedule
  python earner.py status              print status + refresh dashboard data
  python earner.py earn --strategy affiliate_content --amount 25.40
                                       log REAL money you received
  python earner.py reset-paper         restart the paper trading wallet
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))  # stable relative paths


def main():
    ap = argparse.ArgumentParser(prog="earner", description="Autonomous earning agent")
    sub = ap.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="run strategies")
    p_run.add_argument("--once", action="store_true", help="single cycle then exit")
    p_run.add_argument("--interval", type=float, default=None, help="minutes between cycles")

    sub.add_parser("status", help="show survival status")
    sub.add_parser("reset-paper", help="reset paper trading wallet to $100")

    p_earn = sub.add_parser("earn", help="record real money received")
    p_earn.add_argument("--strategy", required=True)
    p_earn.add_argument("--amount", type=float, required=True)
    p_earn.add_argument("--note", default="")
    p_earn.add_argument("--currency", default="USD")

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 0

    from core.agent import Agent
    from core.utils import setup_logging, today_str

    setup_logging()

    if args.cmd == "run":
        agent = Agent()
        if args.once:
            agent.run_once()
        else:
            agent.run_forever(args.interval)

    elif args.cmd == "status":
        agent = Agent()
        s = agent.write_status()["survival"]
        w = agent.status_payload()["paper_wallet"] or {}
        pos = w.get("position")
        print("=" * 58)
        print(f" {agent.config['agent']['name']}  -  day {s['streak_days']} streak "
              f"| {'ALIVE' if s['alive'] else 'NOT alive yet'}")
        print("=" * 58)
        print(f" real earned today : ${s['real_earned_today']:.2f} / ${s['target_usd']:.2f}"
              f"  ({s['progress_pct']}%)")
        print(f" paper pnl today   : ${s['paper_pnl_today']:+.2f}")
        if isinstance(w, dict):
            eq = w.get("usd", 0) + (pos["qty"] * pos["entry"] if pos else 0)
            print(f" paper wallet      : ${eq:.2f}" + (" (position open)" if pos else ""))
        print("-" * 58)
        for st, d in agent.status_payload()["per_strategy_today"].items():
            earn = d.get("earn", 0.0)
            lead = int(d.get("lead", 0) or 0)
            print(f" {st:<20} real ${earn:>7.2f}   leads today: {lead}")
        print("=" * 58)

    elif args.cmd == "earn":
        from core.ledger import Ledger
        led = Ledger()
        led.record(args.strategy, "earn", args.amount, args.currency, args.note)
        total = led.day_total(kinds=("earn",))
        target = 10.0
        try:
            import yaml
            with open("config.yaml", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            target = float(cfg.get("survival", {}).get("daily_target_usd", target))
        except Exception:  # noqa: BLE001
            pass
        print(f"+ ${args.amount:.2f} logged ({args.strategy}). Today: ${total:.2f} "
              f"/ ${target:.2f} -> {'ALIVE' if total >= target else 'keep going'}")

    elif args.cmd == "reset-paper":
        from core.ledger import Ledger
        Ledger().reset_paper_wallet(100.0)
        print("paper wallet reset to $100.00")
    return 0


if __name__ == "__main__":
    sys.exit(main())
