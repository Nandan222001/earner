"""Strategy registry. Add new earners here."""
from .trading_bot import TradingBot
from .affiliate_content import AffiliateContent
from .micro_tasks import MicroTasks
from .bounty_scout import BountyScout

REGISTRY = {
    "trading_bot": TradingBot,
    "affiliate_content": AffiliateContent,
    "micro_tasks": MicroTasks,
    "bounty_scout": BountyScout,
}


def build_enabled(global_config):
    """Instantiate every strategy whose config block says enabled: true."""
    instances = []
    blocks = global_config.get("strategies", {}) or {}
    for name, cls in REGISTRY.items():
        cfg = blocks.get(name) or {}
        if cfg.get("enabled", False):
            instances.append(cls(cfg))
    return instances
