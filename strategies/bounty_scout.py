"""Strategy: scout unpaid GitHub issues with bounty labels from public APIs (no key)."""
import os
from urllib.parse import quote_plus

from .base import BaseStrategy
from core.utils import http_get_json, now_iso, slugify

QUERIES = [
    'label:bounty state:open',
    'label:"help wanted" label:bounty state:open',
    '"good first issue" label:bounty state:open',
]


class BountyScout(BaseStrategy):
    name = "bounty_scout"
    description = "Finds open GitHub issues with bounty labels and drafts claim notes."

    SEEN_KEY = "bounty_seen_ids"

    def run(self, ctx):
        skills = [s.lower() for s in self.opt("skills", [])]
        max_leads = int(self.opt("max_leads_per_run", 5))
        seen = set(ctx.ledger.get_state(self.SEEN_KEY, []))
        issues = []
        queries = list(QUERIES[:2])
        queries.append('label:"good first issue" state:open language:Python')
        for q in queries:
            if skills and "language:" not in q:
                q = q + " " + " ".join(skills[:3])
            try:
                issues.extend(self._search(q))
            except Exception as e:  # noqa: BLE001
                ctx.log.warning("[bounty_scout] search failed: %s", e)

        unique, ids = [], set()
        for iss in issues:
            if iss["id"] in seen or iss["id"] in ids:
                continue
            ids.add(iss["id"])
            unique.append(iss)
        unique.sort(key=lambda x: -x["score"])
        top = unique[:max_leads]
        leads = []
        for iss in top:
            fname = f"{slugify(iss['id'], 24)}-{slugify(iss['title'], 40)}.md"
            path = os.path.join(ctx.out_gigs, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._note(iss))
            ctx.ledger.record(
                "bounty_scout", "lead", 0,
                note=f"{iss['title']} | {iss['repo']} | {iss['url']}",
            )
            leads.append({"title": iss["title"], "repo": iss["repo"],
                          "url": iss["url"], "score": iss["score"], "file": fname})

        seen |= {i["id"] for i in issues}
        ctx.ledger.set_state(self.SEEN_KEY, sorted(seen)[-800:])
        if top:
            ctx.notify(f"Earner found {len(top)} GitHub bounties, best: {top[0]['title']}")
        return {"ok": True, "scanned": len(issues), "new_matches": len(leads),
                "leads": leads, "checked_at": now_iso()}

    def _search(self, query):
        url = ("https://api.github.com/search/issues?q="
               + quote_plus(query) + "&sort=updated&order=desc&per_page=15")
        data = http_get_json(
            url,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "EarnerAgent/1.0"},
        )
        out = []
        for it in (data or {}).get("items") or []:
            repo_url = (it.get("repository_url") or "")
            repo = repo_url.replace("https://api.github.com/repos/", "")
            labels = [str(l.get("name", "")).lower() for l in (it.get("labels") or [])]
            comments = int(it.get("comments") or 0)
            score = 10 + (8 if "bounty" in labels else 0) + min(comments, 20)
            out.append({
                "id": str(it.get("html_url") or it.get("id")),
                "title": str(it.get("title") or "").strip(),
                "repo": repo or "?",
                "url": it.get("html_url") or "",
                "stars": 0,
                "labels": labels[:8],
                "score": round(score, 1),
                "body": (it.get("body") or "")[:600],
            })
        return out

    def _note(self, iss):
        labs = ", ".join(iss.get("labels") or []) or "none"
        return f"""# Bounty claim draft - {iss['title']}
Repo    : {iss['repo']}
Link    : {iss['url']}
Labels  : {labs}
Score   : {iss['score']}
Generated: {now_iso()} by Earner agent

---
Hi maintainers,

I would like to claim this bounty / help-wanted issue.

Plan:
1. Reproduce the issue and write a failing test or repro steps.
2. Implement the smallest correct fix.
3. Open a PR with a clear description and linked issue.

I can start immediately. Please confirm the bounty is still open before I begin.

Best regards,
(Your name - edit before sending!)

Issue excerpt:
{iss.get('body') or '(no body)'}
"""
