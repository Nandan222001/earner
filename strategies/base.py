"""Base class every earning strategy must inherit from."""


class BaseStrategy:
    name = "base"
    description = ""

    def __init__(self, config=None):
        self.config = config or {}

    def run(self, ctx):
        """Execute one earning cycle.

        ctx: core.agent.AgentContext (has .config, .ledger, .log, .notify,
             .out_articles, .out_gigs). Must return a JSON-serializable dict.
        """
        raise NotImplementedError

    # -- helpers -------------------------------------------------------
    def opt(self, key, default=None):
        v = self.config.get(key, default)
        return default if v is None else v
