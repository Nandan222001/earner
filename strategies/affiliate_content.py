"""Strategy: writes SEO articles around your affiliate links; optional WP publish."""
import os
import random
from urllib.parse import quote_plus

from .base import BaseStrategy
from core.utils import http_post_json, now_iso, strip_html, today_str

TOPICS = [
    ("best-budget-standing-desks", "standing desk", "Best Budget Standing Desks Under $200"),
    ("cheap-mechanical-keyboards", "mechanical keyboard", "7 Cheap Mechanical Keyboards That Feel Premium"),
    ("home-office-upgrades", "home office", "10 Home Office Upgrades That Pay for Themselves"),
    ("budget-noise-cancelling-earbuds", "earbuds", "Best Noise-Cancelling Earbuds on a Tight Budget"),
    ("beginner-web-scraping-tools", "web scraping", "Beginner Web Scraping Tools Compared (2026)"),
    ("laptops-for-freelancers", "freelancer laptop", "Best Laptops for Freelancers Under $600"),
    ("productivity-apps-side-hustle", "productivity app", "9 Productivity Apps Every Side Hustler Needs"),
    ("cheap-vpn-guide", "vpn", "Do You Still Need a VPN in 2026? An Honest Guide"),
    ("smartphone-gimbal-buyers-guide", "gimbal", "Smartphone Gimbals: A Buyer's Guide for Creators"),
    ("ergonomic-chair-under-300", "ergonomic chair", "The Best Ergonomic Chairs Under $300, Tested"),
]

INTRO = [
    "Working from a small budget does not mean settling for bad gear.",
    "Upgrading your setup should not wreck your wallet.",
    "After comparing dozens of options, these picks stand out for value.",
]
PICK_BLURB = [
    "Consistently strong reviews and a price that is hard to beat.",
    "The best balance of durability and cost we could find.",
    "A favorite among buyers who want premium feel without the premium tag.",
]
FAQS = [
    ("Is buying online safe for this category?",
     "Yes - stick to reputable sellers and check recent reviews before ordering."),
    ("When is the best time to buy?",
     "Major sale events usually drop prices 15-30% versus normal weeks."),
    ("How long do these typically last?",
     "With normal use, expect several years; warranty terms are linked on each product page."),
]


class AffiliateContent(BaseStrategy):
    name = "affiliate_content"
    description = "Generates SEO articles with monetized links."

    def run(self, ctx):
        n = int(self.opt("articles_per_run", 2))
        site = self.opt("site_name", "Earner Blog")
        rng = random.Random(f"{today_str()}|{site}")
        picks = rng.sample(TOPICS, k=min(n, len(TOPICS)))
        articles, published = [], []
        for slug, keyword, title in picks:
            blocks = self._blocks(title, keyword, rng)
            html = self._render_html(title, blocks, site)
            fname = f"{today_str()}-{slug}.html"
            with open(os.path.join(ctx.out_articles, fname), "w", encoding="utf-8") as f:
                f.write(html)
            words = sum(len(b[1].split()) for b in blocks)
            ctx.ledger.record("affiliate_content", "lead", 0,
                              note=f"article '{title}' ({words} words) -> data/output/articles/{fname}")
            articles.append({"file": fname, "title": title, "words": words})
            url = self._publish_wordpress(title, html)
            if url:
                published.append(url)
                ctx.notify(f"Published article: {title}")
        return {"ok": True, "articles": articles, "published": published}

    # -------------------------------------------------- generation ---
    def _blocks(self, title, keyword, rng):
        link = self._link(keyword)
        b = [("p", f"{rng.choice(INTRO)} This guide covers the best value {keyword} options, "
                   f"updated {today_str()}.")]
        b.append(("h2", f"Why the right {keyword} matters"))
        for s in ["It affects your daily comfort and output more than almost any other purchase.",
                  "Cheap alternatives often cost more over time through replacements.",
                  "A good pick holds resale value surprisingly well."]:
            b.append(("li", s))
        b.append(("h2", "What to look for"))
        for s in [f"Build quality - read recent buyer reviews for the specific {keyword} model.",
                  "Warranty length and how easy the seller is to deal with.",
                  "Total cost including shipping and accessories.",
                  "Real-world performance, not just spec-sheet numbers."]:
            b.append(("li", s))
        b.append(("h2", "Top picks"))
        for i, blurb in enumerate(rng.sample(PICK_BLURB, 3), 1):
            line = f"{i}. A reliable {keyword} option: {blurb}"
            if link:
                line += f' <a href="{link}" rel="sponsored nofollow">Check current price</a>.'
            else:
                line += " *(add your affiliate link here)*"
            b.append(("p", line))
        b.append(("h2", "Frequently asked questions"))
        for q, a in FAQS:
            b.append(("p", f"**{q}** {a}"))
        b.append(("p", "Links above may earn us a commission at no extra cost to you - "
                       "it keeps honest reviews like this one free."))
        return b

    def _link(self, keyword):
        tag = self.opt("amazon_tag")
        if tag:
            return f"https://www.amazon.com/s?k={quote_plus(keyword)}&tag={tag}"
        for cl in (self.opt("custom_links") or []):
            kws = " ".join(cl.get("keywords", [])).lower()
            if keyword in kws or any(k in keyword for k in kws.split()):
                return cl.get("url")
        return None

    @staticmethod
    def _render_html(title, blocks, site):
        body = []
        for kind, text in blocks:
            if kind == "h2":
                body.append(f"<h2>{text}</h2>")
            elif kind == "li":
                body.append(f"<li>{text}</li>")
            else:
                body.append(f"<p>{text}</p>")
        css = ("body{font-family:Georgia,serif;max-width:720px;margin:40px auto;"
               "padding:0 16px;line-height:1.65;color:#222}a{color:#0a66c2}")
        return (f"<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>{title}</title><style>{css}</style></head>"
                f"<body><h1>{title}</h1>"
                f"<p><em>{site} | {now_iso()}</em></p>{''.join(body)}"
                f"</body></html>")

    # --------------------------------------------------- wordpress ---
    def _publish_wordpress(self, title, html):
        wp = self.opt("wordpress", {}) or {}
        if not wp.get("enabled"):
            return None
        try:
            resp = http_post_json(
                wp["url"].rstrip("/") + "/wp-json/wp/v2/posts",
                payload={"title": title, "content": strip_html(html)[:20000], "status": "draft"},
                auth=(wp["username"], wp["app_password"]))
            data = resp.json()
            if resp.status_code in (200, 201):
                return data.get("link") or f"post #{data.get('id')}"
        except Exception:  # noqa: BLE001
            pass
        return None

