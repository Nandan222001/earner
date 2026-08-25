# Contributing to Earner

Thanks for helping the agent hustle harder! 🤝

## Dev setup

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v     # must be green
python tests/simulate_trading.py            # sanity-check the trader
```

## Adding a strategy (the main way to contribute)

1. Create `strategies/my_strategy.py`:

```python
from .base import BaseStrategy

class MyStrategy(BaseStrategy):
    name = "my_strategy"

    def run(self, ctx):
        # ctx.config -> your YAML block, ctx.ledger -> record results,
        # ctx.notify(text) -> webhook alert, ctx.log -> logger
        ctx.ledger.record(self.name, "lead", 0, note="found something")
        return {"ok": True}
```

2. Register it in `strategies/__init__.py` → `REGISTRY`.
3. Add an `enabled:` config block to `config.yaml` and document it in the README table.
4. Add unit tests in `tests/test_core.py`.

## Ground rules

- **Stdlib-first**: `requests` and `PyYAML` are the only hard deps — keep it that way.
  Heavy libs (ccxt, openai) must stay lazy-imported and optional.
- **Never fake earnings**: simulated results use `paper_earn`; opportunities are `lead`.
  Only real received money gets kind=`earn`.
- Respect platform ToS — no scraping behind logins, no spam tooling.
- Run the full test suite before opening a PR.
