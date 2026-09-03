"""Strategy: scout real remote gigs from keyless public feeds, draft proposals."""
import os
import re
import xml.etree.ElementTree as ET

from .base import BaseStrategy
from core.utils import http_get_json, http_get_text, now_iso, slugify, strip_html

MONEY_RE = re.compile(
    r"(?:\$|USD\s?)([\d][\d,]*)\s*(k?)"
    r"(?:\s*[-\u2013]\s*(?:\$|USD\s?)?([\d][\d,]*)\s*(k?))?", re.I)


def parse_salary(text):
    """Rough parse of '$60,000-80,000', '$25/hr', '$80k' -> {'annual','hourly'}."""
    m = MONEY_RE.search(text or "")
    if not m:
        return None
    val = lambda s, k: float(s.replace(",", "")) * (1000 if k.lower() == "k" else 1)  # noqa: E731
    hi = val(m.group(3) or m.group(1), m.group(4) or m.group(2))
    if hi <= 500:                      # looks like an hourly figure
        return {"annual": round(hi * 2080, 2), "hourly": hi}
    return {"annual": hi, "hourly": round(hi / 2080, 2)}


class MicroTasks(BaseStrategy):
    name = "micro_tasks"
    description = "Finds remote gigs matching your skills and writes proposals."

    SEEN_KEY = "gig_seen_ids"

    def run(self, ctx):
        skills = [s.lower() for s in self.opt("skills", [])]
        min_hourly = float(self.opt("min_hourly_usd", 0))
        max_leads = int(self.opt("max_leads_per_run", 5))
        feeds = self.opt("feed_urls", [])

        jobs, scanned = [], 0
        for url in feeds:
            try:
                jobs.extend(self._fetch(url))
            except Exception as e:  # noqa: BLE001
                ctx.log.warning("[micro_tasks] feed failed %s : %s", url, e)
        try:
            jobs.extend(self._builtin_feeds())
        except Exception as e:  # noqa: BLE001
            ctx.log.warning("[micro_tasks] builtin feeds failed: %s", e)
        scanned = len(jobs)

        seen = set(ctx.ledger.get_state(self.SEEN_KEY, []))
        scored = []
        for j in jobs:
            if not isinstance(j, dict) or not j.get("id"):
                continue
            jid = j["id"]
            if jid in seen:
                continue
            hay = (j["title"] + " " + j["description"]).lower()
            hits = sorted({s for s in skills if s in hay})
            sal = parse_salary(j["description"]) or {}
            hourly = sal.get("hourly")
            score = len(hits) * 10 + (5 if hourly and hourly >= min_hourly else 0)
            if hits:
                scored.append({**j, "hits": hits, "score": score,
                               "hourly": hourly, "annual": sal.get("annual")})

        scored.sort(key=lambda x: -x["score"])
        top = scored[:max_leads]
        leads = []
        for j in top:
            fname = f"{slugify(j['id'], 30)}-{slugify(j['title'], 40)}.md"
            path = os.path.join(ctx.out_gigs, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._proposal(j, skills))
            est = f"~${j['hourly']}/hr" if j["hourly"] else "salary n/a"
            ctx.ledger.record("micro_tasks", "lead", 0,
                              note=f"{j['title']} @ {j['company']} | {est} | score={j['score']} | {j['url']}")
            leads.append({"title": j["title"], "company": j["company"], "url": j["url"],
                          "score": j["score"], "hourly": j["hourly"], "file": fname})

        seen |= {j["id"] for j in jobs if isinstance(j, dict) and j.get("id")}
        ctx.ledger.set_state(self.SEEN_KEY, sorted(seen)[-800:])
        if top:
            ctx.notify(f"Earner found {len(top)} new matching gigs, best: "
                       f"{top[0]['title']} ({top[0]['company']})")
        return {"ok": True, "scanned": scanned, "new_matches": len(leads),
                "leads": leads, "checked_at": now_iso()}

    # --------------------------------------------------------------

    def _builtin_feeds(self):
        """Keyless public APIs that always run in addition to config feed_urls."""
        out = []
        for fn in (self._arbeitnow, self._hackernews_jobs, self._remoteok, self._hn_who_is_hiring):
            try:
                out.extend(fn())
            except Exception:  # noqa: BLE001
                continue
        return out

    def _arbeitnow(self):
        data = http_get_json("https://www.arbeitnow.com/api/job-board-api")
        items = data.get("data") if isinstance(data, dict) else data
        out = []
        for j in (items or [])[:80]:
            if not isinstance(j, dict) or not j.get("title"):
                continue
            out.append({
                "id": str(j.get("slug") or j.get("url") or j["title"]),
                "title": str(j["title"]).strip(),
                "company": str(j.get("company_name") or "?").strip(),
                "url": j.get("url") or "",
                "description": strip_html(j.get("description") or "")[:1200],
            })
        return out

    def _hackernews_jobs(self):
        ids = http_get_json("https://hacker-news.firebaseio.com/v0/jobstories.json") or []
        out = []
        for hid in ids[:6]:
            try:
                item = http_get_json(f"https://hacker-news.firebaseio.com/v0/item/{hid}.json")
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(item, dict) or not item.get("title"):
                continue
            text = strip_html(item.get("text") or "")
            url = item.get("url") or f"https://news.ycombinator.com/item?id={hid}"
            out.append({
                "id": f"hn-{hid}",
                "title": str(item["title"]).strip(),
                "company": "Hacker News",
                "url": url,
                "description": text[:1200],
            })
        return out

    def _remoteok(self):
        data = http_get_json(
            "https://remoteok.com/api",
            headers={"User-Agent": "EarnerAgent/1.0 (job scout; +https://github.com)"},
        )
        out = []
        for j in (data or [])[:80]:
            if not isinstance(j, dict) or not j.get("position"):
                continue
            desc = strip_html(j.get("description") or " ".join(j.get("tags") or []))
            out.append({
                "id": str(j.get("id") or j.get("slug") or j["position"]),
                "title": str(j["position"]).strip(),
                "company": str(j.get("company") or "?").strip(),
                "url": j.get("url") or j.get("apply_url") or "",
                "description": desc[:1200],
            })
        return out

    def _hn_who_is_hiring(self):
        data = http_get_json(
            "https://hn.algolia.com/api/v1/search_by_date?query=hiring&tags=story&hitsPerPage=20")
        out = []
        for h in (data or {}).get("hits") or []:
            title = h.get("title") or ""
            if "hiring" not in title.lower() and "freelancer" not in title.lower():
                continue
            hid = h.get("objectID") or h.get("story_id") or title
            out.append({
                "id": f"hn-algolia-{hid}",
                "title": str(title).strip(),
                "company": "Hacker News",
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={hid}",
                "description": strip_html(h.get("story_text") or title)[:1200],
            })
        return out

    def _fetch(self, url):
        out = []
        body = http_get_text(url)
        if body.lstrip().startswith("{") or body.lstrip().startswith("["):
            data = http_get_json(url)
            items = self._json_items(data)
            for j in (items or []):
                parsed = self._normalize_job(j)
                if parsed:
                    out.append(parsed)
            return out
        root = ET.fromstring(body)  # RSS/Atom fallback
        for item in root.iter():
            tag = item.tag.rsplit("}", 1)[-1]
            if tag not in ("item", "entry"):
                continue
            gett = lambda t, el=item: (el.findtext(t) or el.findtext(
                "{http://www.w3.org/2005/Atom}" + t) or "")  # noqa: E731
            title = strip_html(gett("title"))
            link = gett("link")
            if not link:
                child = item.find("{http://www.w3.org/2005/Atom}link")
                if child is not None:
                    link = child.get("href") or ""
            if title:
                out.append({
                    "id": gett("guid") or gett("id") or title,
                    "title": title.strip(),
                    "company": "?",
                    "url": link,
                    "description": strip_html(gett("description") or gett("summary") or gett("content"))[:1200],
                })
        return out

    @staticmethod
    def _json_items(data):
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []
        for key in ("jobs", "data", "results", "items"):
            if isinstance(data.get(key), list):
                return data[key]
        return []

    @staticmethod
    def _normalize_job(j):
        if not isinstance(j, dict):
            return None
        title = j.get("title") or j.get("position") or j.get("name") or j.get("jobTitle")
        if not title:
            return None
        company = j.get("company_name") or j.get("companyName") or j.get("company") or "?"
        if isinstance(company, dict):
            company = company.get("name") or "?"
        url = (j.get("url") or j.get("link") or j.get("jobUrl")
               or (j.get("refs") or {}).get("landing_page") or "")
        desc = j.get("description") or j.get("jobDescription") or j.get("contents") or ""
        return {
            "id": str(j.get("id") or j.get("slug") or j.get("jobId") or title),
            "title": str(title).strip(),
            "company": str(company).strip(),
            "url": url,
            "description": strip_html(desc)[:1200],
        }

    def _proposal(self, j, skills):
        why = "\n".join(f"- Hands-on experience with **{s}**." for s in j["hits"]) or \
              "- Fast learner, available immediately."
        rate = f"${j['hourly']}/hr" if j["hourly"] else "your budget"
        return f"""# Proposal draft - {j['title']}
Company : {j['company']}
Link    : {j['url']}
Match   : score {j['score']} via skills {', '.join(j['hits'])}
Generated: {now_iso()} by Earner agent

---
Hi {j['company']} team,

I read your posting for **{j['title']}** carefully and it is a strong fit:
{why}

A few things I would deliver in the first week:
1. A short plan confirming scope, milestones and communication cadence.
2. A working first version of the core requirement.
3. Clean handover docs so your team can maintain it.

I can start right away and am flexible around {rate}. Happy to jump on a
quick call to align on details.

Best regards,
(Your name - edit before sending!)
"""
