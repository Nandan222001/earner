"""Strategy: SMA-crossover crypto trader. Paper mode default; live needs keys."""
from .base import BaseStrategy
from core.utils import http_get_json


class TradingBot(BaseStrategy):
    name = "trading_bot"
    description = "Hourly SMA(6)/SMA(24) crossover crypto trader."

    WALLET_KEY = "paper_wallet"

    def run(self, ctx):
        symbol = str(self.opt("symbol", "BTCUSDT")).upper()
        mode = str(self.opt("mode", "paper")).lower()
        closes = self._hourly_closes(symbol)
        if len(closes) < 30:
            return {"ok": False, "error": "not enough price data (all sources failed?)"}
        price = closes[-1]
        fast = sum(closes[-6:]) / 6.0
        slow = sum(closes[-24:]) / 24.0
        signal = "buy" if fast > slow else "wait"
        if mode == "live":
            return self._live(ctx, symbol, price, signal)
        return self._paper(ctx, symbol, price, signal)

    # ------------------------------------------------------- paper ---
    def _paper(self, ctx, symbol, price, signal):
        stake = float(self.opt("stake_usd_per_trade", 5))
        tp = float(self.opt("take_profit_pct", 2)) / 100.0
        sl = float(self.opt("stop_loss_pct", 1.5)) / 100.0
        max_loss = abs(float(self.opt("max_daily_loss_usd", 2)))

        w = ctx.ledger.get_state(self.WALLET_KEY) or {"usd": 100.0, "position": None}
        actions = []
        pos = w.get("position")

        if pos:
            move = price / pos["entry"] - 1.0
            if move >= tp or move <= -sl:
                proceeds = pos["qty"] * price
                pnl = proceeds - pos["stake"]
                w["usd"] += proceeds
                kind = "paper_earn" if pnl >= 0 else "spend"
                ctx.ledger.record("trading_bot", kind, abs(round(pnl, 4)),
                                  note=f"PAPER {symbol} {pos['entry']:.2f} -> {price:.2f} ({pnl:+.2f})")
                actions.append(f"closed @ {price:.2f}, pnl {pnl:+.2f}")
                w["position"] = None

        if not w.get("position") and signal == "buy":
            pnl_today = (ctx.ledger.day_total(kinds=("paper_earn",))
                         - ctx.ledger.day_total(kinds=("spend",)))
            if pnl_today <= -max_loss:
                actions.append(f"daily loss cap ({max_loss}) reached -> no new trades today")
            elif w["usd"] >= stake:
                qty = stake / price
                w["usd"] -= stake
                w["position"] = {"qty": qty, "entry": price, "stake": stake}
                actions.append(f"BUY {qty:.6f} @ {price:.2f}")

        ctx.ledger.set_state(self.WALLET_KEY, w)
        pos_now = w.get("position")
        equity = w["usd"] + (pos_now["qty"] * price if pos_now else 0.0)
        return {"ok": True, "mode": "paper", "symbol": symbol, "price": round(price, 2),
                "signal": signal, "actions": actions,
                "wallet_usd": round(w["usd"], 2), "equity_usd": round(equity, 2)}

    # -------------------------------------------------------- live ---
    def _live(self, ctx, symbol, price, signal):
        key, secret = self.opt("api_key"), self.opt("api_secret")
        if not key or not secret:
            return {"ok": False, "error": "mode=live but api_key/api_secret empty in config"}
        if not self.config.get("i_understand_trading_risk", False):
            return {"ok": False, "error": "set i_understand_trading_risk: true to enable live orders"}
        try:
            import ccxt
        except ImportError:
            return {"ok": False, "error": "run: pip install ccxt (needed for live mode)"}
        stake = float(self.opt("stake_usd_per_trade", 5))
        tp = float(self.opt("take_profit_pct", 2)) / 100.0
        sl = float(self.opt("stop_loss_pct", 1.5)) / 100.0
        try:
            ex = getattr(ccxt, str(self.opt("exchange", "binance")))(
                {"apiKey": key, "secret": secret, "enableRateLimit": True})
            pos = ctx.ledger.get_state("live_position")
            if pos:
                move = price / pos["entry"] - 1.0
                if move >= tp or move <= -sl:
                    ex.create_market_sell_order(symbol, pos["qty"])
                    pnl = pos["qty"] * price - pos["stake"]
                    kind = "earn" if pnl >= 0 else "spend"
                    ctx.ledger.record("trading_bot", kind, abs(round(pnl, 4)),
                                      note=f"LIVE {symbol} closed @ {price:.2f} ({pnl:+.2f})")
                    ctx.ledger.set_state("live_position", None)
                    ctx.notify(f"[LIVE] closed {symbol}, pnl {pnl:+.2f} USD")
                    return {"ok": True, "mode": "live", "action": f"closed, pnl {pnl:+.2f}"}
                return {"ok": True, "mode": "live", "action": "holding"}
            bal = ex.fetch_balance()
            quote_ccy = symbol[-4:] if symbol.endswith("USDT") else "USD"
            free_quote = float(bal.get("total", {}).get(quote_ccy, 0) or 0)
            if signal == "buy" and free_quote >= stake:
                qty = round(stake / price, 6)
                ex.create_market_buy_order(symbol, qty)
                ctx.ledger.set_state("live_position", {"qty": qty, "entry": price, "stake": stake})
                ctx.notify(f"[LIVE] bought {qty} {symbol} @ ~{price:.2f}")
                return {"ok": True, "mode": "live", "action": f"bought {qty}"}
            return {"ok": True, "mode": "live",
                    "action": f"no trade (signal={signal}, free={free_quote})"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"exchange error: {e}"}

    # ------------------------------------------------------ prices ---
    def _hourly_closes(self, symbol):
        base, quote = symbol, "USD"
        for q in ("USDT", "USD", "BUSD"):
            if symbol.endswith(q):
                base, quote = symbol[: -len(q)], q
                break
        try:  # Binance public API (no key needed)
            data = http_get_json(
                f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit=72")
            return [float(k[4]) for k in data]
        except Exception:  # noqa: BLE001
            pass
        try:  # CryptoCompare public fallback (no key needed)
            d = http_get_json(
                f"https://min-api.cryptocompare.com/data/v2/histohour"
                f"?fsym={base}&tsym={quote}&limit=72")
            return [float(c["close"]) for c in d["Data"]["Data"]]
        except Exception:  # noqa: BLE001
            pass
        try:  # CoinGecko public fallback (no key needed)
            cg_id = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}.get(base.upper())
            if not cg_id:
                return []
            d = http_get_json(
                f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart"
                f"?vs_currency=usd&days=3&interval=hourly")
            prices = d.get("prices") or []
            return [float(p[1]) for p in prices[-72:]]
        except Exception:  # noqa: BLE001
            return []

