"""Strategy: scout Reddit hiring posts (public JSON, no key) and draft replies."""
import os
import re
from urllib.parse import quote_plus

from .base import BaseStrategy
from core.utils import http_get_json, now_iso, slugify, strip_html

DEFAULT_SUBS = [
    "forhire",
    "slavelabour",
    "hiring",
    "jobbit",
    "freelance_forhire",
    "HireaWriter",
    "WebdevJobs",
    "remotejs",
    "PythonJobs",
]

DEFAULT_KEYWORDS = [
    "website", "web development", "wordpress", "landing page", "shopify",
    "app", "mobile app", "react native", "flutter",
    "ai", "chatgpt", "openai", "llm", "automation", "chatbot", "integration",
    "next.js", "react", "python", "api",
]

HIRE_RE = re.compile(
    r"\[?\s*(hiring|hire|looking for|need(s|ed)?|seeking|paid|budget|for hire)\s*\]?",
    re.I,
)
SKIP_RE = re.compile(r"\[?\s*(for hire|hire me|available)\s*\]?", re.I)


class RedditScout(BaseStrategy):
    name = "reddit_scout"
    description = "Finds Reddit hiring posts for web, app, and AI work."

    SEEN_KEY = "reddit_seen_ids"

    def run(self, ctx):
        subs = [s.strip().removeprefix("r/").removeprefix("/r/") for s in self.opt("subreddits", DEFAULT_SUBS) if s]
        keywords = [k.lower() for k in self.opt("keywords", DEFAULT_KEYWORDS)]
        max_leads = int(self.opt("max_leads_per_run", 8))
        seen = set(ctx.ledger.get_state(self.SEEN_KEY, []) or [])
        posts = []
        reddit_ok = False
        if self.opt("try_reddit", False):
            for sub in subs[:3]:
                try:
                    got = self._listing(sub)
                    if got:
                        reddit_ok = True
                        posts.extend(got)
                except Exception as e:  # noqa: BLE001
                    ctx.log.warning("[reddit_scout] json failed r/%s: %s", sub, e)
        if not reddit_ok:
            ctx.log.info("[reddit_scout] Reddit JSON skipped/blocked; using HN Algolia hiring threads")
        try:
            posts.extend(self._hn_seeking_freelancer(keywords))
        except Exception as e:  # noqa: BLE001
            ctx.log.warning("[reddit_scout] HN freelancer fallback failed: %s", e)
        try:
            posts.extend(self._hn_who_is_hiring(keywords))
        except Exception as e:  # noqa: BLE001
            ctx.log.warning("[reddit_scout] HN who-is-hiring failed: %s", e)

        unique, ids = [], set()
        for p in posts:
            if not isinstance(p, dict) or not p.get("id"):
                continue
            if p["id"] in seen or p["id"] in ids:
                continue
            ids.add(p["id"])
            hay = (p["title"] + " " + p["body"]).lower()
            if SKIP_RE.search(p["title"]) and not HIRE_RE.search(p["title"]):
                continue
            hits = sorted({k for k in keywords if k in hay})
            if not hits and p.get("sub") != "hackernews":
                continue
            if not hits:
                hits = [k for k in ("website", "app", "ai") if k in hay] or ["freelance"]
            hiring = bool(HIRE_RE.search(p["title"]) or HIRE_RE.search(p["body"][:400]))
            score = len(hits) * 8 + (12 if hiring else 0) + min(int(p.get("comments") or 0), 15)
            if p.get("sub") == "hn_hiring":
                score += 40
            if p.get("title", "").lower().startswith(("show hn", "launch hn")):
                score -= 30
            unique.append({**p, "hits": hits, "score": score, "hiring": hiring})

        unique.sort(key=lambda x: -x["score"])
        top = unique[:max_leads]
        out_dir = getattr(ctx, "out_gigs", os.path.join("data", "output", "gigs"))
        os.makedirs(out_dir, exist_ok=True)
        leads = []
        for p in top:
            fname = f"reddit-{slugify(p['sub'], 20)}-{slugify(p['id'], 12)}-{slugify(p['title'], 36)}.md"
            path = os.path.join(out_dir, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._reply(p))
            ctx.ledger.record(
                "reddit_scout", "lead", 0,
                note=f"[r/{p['sub']}] {p['title']} | {p['url']}",
            )
            leads.append({
                "title": p["title"], "sub": p["sub"], "url": p["url"],
                "score": p["score"], "hits": p["hits"], "file": fname,
            })

        seen |= {p["id"] for p in posts if isinstance(p, dict) and p.get("id")}
        ctx.ledger.set_state(self.SEEN_KEY, sorted(seen)[-800:])
        if top:
            ctx.notify(f"Earner found {len(top)} Reddit hiring posts, best: {top[0]['title']}")
        return {"ok": True, "scanned": len(posts), "new_matches": len(leads),
                "leads": leads, "checked_at": now_iso()}

    def _headers(self):
        return {
            "User-Agent": "EarnerAgent/1.0 (lead scout; contact: local)",
            "Accept": "application/json",
        }

    def _listing(self, sub):
        for host in ("https://old.reddit.com", "https://www.reddit.com"):
            data = http_get_json(
                f"{host}/r/{sub}/new.json?limit=40&raw_json=1",
                headers=self._headers(),
                retries=0,
                timeout=12,
            )
            parsed = self._parse_listing(data, sub)
            if parsed:
                return parsed
        return []

    def _rss(self, sub):
        from xml.etree import ElementTree as ET
        from core.utils import http_get_text
        body = http_get_text(
            f"https://old.reddit.com/r/{sub}/new.rss?limit=40",
            headers={"User-Agent": "EarnerAgent/1.0 (lead scout)", "Accept": "application/rss+xml"},
        )
        root = ET.fromstring(body)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        out = []
        entries = list(root.findall("a:entry", ns)) or list(root.iter())
        for el in entries:
            tag = el.tag.rsplit("}", 1)[-1]
            if tag not in ("entry", "item"):
                continue
            title_el = el.find("a:title", ns) if tag == "entry" else el.find("title")
            title = strip_html((title_el.text if title_el is not None else "") or "")
            if not title:
                continue
            link = ""
            link_el = el.find("a:link", ns) if tag == "entry" else el.find("link")
            if link_el is not None:
                link = link_el.get("href") or (link_el.text or "")
            eid_el = el.find("a:id", ns) if tag == "entry" else el.find("guid")
            eid = (eid_el.text if eid_el is not None else "") or link or title
            summary_el = el.find("a:content", ns) if tag == "entry" else el.find("description")
            body_txt = strip_html((summary_el.text if summary_el is not None else "") or "")
            out.append({
                "id": str(eid)[-12:],
                "title": title.strip(),
                "sub": sub,
                "url": link,
                "body": body_txt[:1500],
                "author": "",
                "comments": 0,
            })
        return out

    def _pullpush(self, sub):
        data = http_get_json(
            f"https://api.pullpush.io/reddit/search/submission/?subreddit={quote_plus(sub)}&size=40",
            headers=self._headers(),
        )
        return self._parse_pullpush(data, sub)

    def _pullpush_search(self, keywords):
        q = " OR ".join(keywords[:5])
        data = http_get_json(
            "https://api.pullpush.io/reddit/search/submission/"
            f"?q={quote_plus(q)}&subreddit=forhire,slavelabour,hiring,jobbit&size=40",
            headers=self._headers(),
        )
        return self._parse_pullpush(data, "?")

    def _parse_pullpush(self, data, default_sub):
        out = []
        items = (data or {}).get("data") if isinstance(data, dict) else data
        for d in items or []:
            if not isinstance(d, dict):
                continue
            title = strip_html(d.get("title") or "")
            if not title:
                continue
            permalink = d.get("permalink") or ""
            url = ("https://www.reddit.com" + permalink) if str(permalink).startswith("/") else (d.get("url") or "")
            out.append({
                "id": str(d.get("id") or permalink or title),
                "title": title.strip(),
                "sub": str(d.get("subreddit") or default_sub),
                "url": url,
                "body": strip_html(d.get("selftext") or "")[:1500],
                "author": str(d.get("author") or ""),
                "comments": int(d.get("num_comments") or 0),
            })
        return out

    def _hn_who_is_hiring(self, keywords):
        import html
        stories = http_get_json(
            "https://hn.algolia.com/api/v1/search_by_date"
            "?query=" + quote_plus("Ask HN: Who is hiring?") + "&tags=story&hitsPerPage=3",
            retries=0, timeout=12,
        )
        sid = None
        for h in (stories or {}).get("hits") or []:
            if "who is hiring" in (h.get("title") or "").lower():
                sid = h.get("objectID")
                break
        if not sid:
            return []
        queries = ["website", "wordpress", "shopify", "frontend", "full-stack",
                   "mobile", "react native", "flutter", "next.js", "AI", "LLM",
                   "chatbot", "contractor", "freelance"]
        out, seen = [], set()
        for q in queries:
            data = http_get_json(
                "https://hn.algolia.com/api/v1/search_by_date"
                f"?query={quote_plus(q)}&tags=comment&hitsPerPage=20"
                f"&numericFilters=story_id={sid}",
                retries=0, timeout=12,
            )
            for c in (data or {}).get("hits") or []:
                oid = str(c.get("objectID") or "")
                if not oid or oid in seen:
                    continue
                seen.add(oid)
                raw = html.unescape(c.get("comment_text") or "")
                body = strip_html(raw)
                if len(body) < 120:
                    continue
                title = body.split(".")[0][:110].strip() or f"HN hiring #{oid}"
                out.append({
                    "id": f"hnc-{oid}",
                    "title": title,
                    "sub": "hn_hiring",
                    "url": f"https://news.ycombinator.com/item?id={oid}",
                    "body": body[:1500],
                    "author": str(c.get("author") or ""),
                    "comments": 0,
                })
        return out

    def _hn_seeking_freelancer(self, keywords):
        queries = [
            "hiring website developer",
            "looking to hire developer",
            "need a website built",
            "need a mobile app developer",
            "hiring AI engineer contractor",
            "seeking freelancer website",
            "Ask HN: looking to hire",
            "Ask HN: Who is hiring?",
        ]
        supply = re.compile(
            r"looking for (freelance|contract|work)|developer looking|who wants to be hired|"
            r"available for hire|for hire\]|hire me",
            re.I,
        )
        demand = re.compile(
            r"\b(hiring|looking to hire|need(s|ed)? a |seeking (a |an )?(freelancer|developer|contractor)|"
            r"who can (build|make)|pay(ing)? (for|\$)|budget)\b",
            re.I,
        )
        out = []
        for q in queries:
            data = http_get_json(
                "https://hn.algolia.com/api/v1/search_by_date"
                f"?query={quote_plus(q)}&tags=story&hitsPerPage=25",
                retries=0,
                timeout=12,
            )
            for h in (data or {}).get("hits") or []:
                title = strip_html(h.get("title") or "")
                if not title:
                    continue
                body = strip_html(h.get("story_text") or "")
                hay = title + " " + body
                if supply.search(title) and not demand.search(title):
                    continue
                if not demand.search(hay):
                    continue
                hid = str(h.get("objectID") or title)
                url = h.get("url") or f"https://news.ycombinator.com/item?id={hid}"
                out.append({
                    "id": f"hn-{hid}",
                    "title": title.strip(),
                    "sub": "hackernews",
                    "url": url if str(url).startswith("http") else f"https://news.ycombinator.com/item?id={hid}",
                    "body": body[:1500] or title,
                    "author": str(h.get("author") or ""),
                    "comments": int(h.get("num_comments") or 0),
                })
        return out

    def _search(self, keywords):
        q = " OR ".join(keywords[:6])
        url = ("https://old.reddit.com/r/forhire+slavelabour+hiring+jobbit/search.json"
               f"?q={quote_plus(q)}&restrict_sr=1&sort=new&t=week&limit=40&raw_json=1")
        data = http_get_json(url, headers=self._headers())
        return self._parse_listing(data, "?")

    def _parse_listing(self, data, default_sub):
        out = []
        children = ((data or {}).get("data") or {}).get("children") or []
        for ch in children:
            d = ch.get("data") if isinstance(ch, dict) else None
            if not isinstance(d, dict) or d.get("stickied"):
                continue
            title = strip_html(d.get("title") or "")
            if not title:
                continue
            permalink = d.get("permalink") or ""
            url = ("https://www.reddit.com" + permalink) if permalink.startswith("/") else (d.get("url") or "")
            body = strip_html(d.get("selftext") or "")
            out.append({
                "id": str(d.get("id") or permalink or title),
                "title": title.strip(),
                "sub": str(d.get("subreddit") or default_sub),
                "url": url,
                "body": body[:1500],
                "author": str(d.get("author") or ""),
                "comments": int(d.get("num_comments") or 0),
            })
        return out

    def _reply(self, p):
        hits = ", ".join(p.get("hits") or [])
        excerpt = (p.get("body") or "").strip()[:700] or "(no body — read the thread)"
        author = p.get("author") or "there"
        role = (p.get("title") or "this role").split("|")[0].strip()[:80]
        return f"""# Reddit reply draft - {p['title']}
Subreddit : r/{p['sub']}
Author    : u/{author}
Link      : {p['url']}
Match     : score {p['score']} | keywords {hits}
Generated : {now_iso()} by Earner agent

---
Hi {author},

I am applying for {role}.

I ship production web apps, mobile clients, and AI integrations (Python/JS, APIs, LLM tooling). I can overlap EST hours, communicate in writing, and start this week.

If this is still open I can send a 1-page note on how I would approach the first 30 days. Happy to jump on a short call.

Replace this line with your name before posting.

---
Post excerpt:
{excerpt}
"""
