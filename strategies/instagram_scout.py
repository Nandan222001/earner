"""Strategy: find Instagram business leads + CONTACT INFO via the official API.

Targets businesses in niches Earner can pitch services to:
  * clinics / healthcare   -> AI voice agent (appointment booking by phone/missed call)
  * shops / local stores   -> website + online ordering + chat/WhatsApp bot
  * general businesses     -> AI chatbot / AI integration on an existing site

Only leads whose caption carries a CONTACT (phone / email / call-us) are
notified, so you can call / WhatsApp / email them straight from your phone.

Uses Meta's official Graph Hashtag Search - no scraping, ToS-safe.
"""
import os
import re

from .base import BaseStrategy
from core.utils import clip, http_get_json, now_iso, slugify

# ---- contact extraction -------------------------------------------------
URL_RE = re.compile(r"https?://\S+|www\.\S+|\binstagram\.com/\S+")
# mobile / WhatsApp (optional +91 / 0 prefix) or landline
PHONE_RE = re.compile(
    r"(?:\+?91[\s.-]?)?(?:0\d{1,4}[\s.-]?)?[6-9]\d{4}[\s.-]?\d{4,5}"
    r"|(?:\(?0\d{2,4}\)?[\s.-]?\d{5,8})"
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def extract_contact(text):
    """Return (phone, email) found in a caption. Robust to URLs / formatting."""
    txt = (text or "")
    clean = URL_RE.sub(" ", txt)
    phone = None
    m = PHONE_RE.search(clean)
    if m:
        phone = re.sub(r"\s+", " ", m.group(0)).strip()
    email = None
    m = EMAIL_RE.search(txt)
    if m:
        email = m.group(0).strip().rstrip(".,;")
    return phone, email


# ---- niche detection (which service to pitch) ----------------------------
# Word-boundary anchored: brand/username noise ("growthlabs", "sparkle",
# "workspace") must never fake a niche - only real business words count.
HEALTH_RE = re.compile(
    r"\b(?:clinic\w*|dent\w*|hospital\w*|doctor\w*|physio\w*|therap\w*|ayurved\w*|"
    r"ortho\w*|cardio\w*|pa?ediatric\w*|gyn\w*|veterinar\w*|vet\b|pharmac\w*|"
    r"medic\w*|laborator\w*|wellness|\bspas?\b|\bsalon\w*|yoga|fitness|"
    r"\bgyms?\b|healthcare|health\s?care|derma\w*|diabet\w*)", re.I)
COMMERCE_RE = re.compile(
    r"\b(?:shops?|stores?|boutiques?|brand\w*|handmade\w*|baker\w*|cafes?|coffee|"
    r"restaurants?|catering|e-?commerce|seller\w*|dropship\w*|retail\w*|food\w*)",
    re.I)


def detect_niche(text):
    t = (text or "")
    if HEALTH_RE.search(t):
        return "health"
    if COMMERCE_RE.search(t):
        return "commerce"
    return "business"


OFFERS = {
    "health":   "voice agent for calls/appointments 24/7 + clinic website",
    "commerce": "website with online ordering + a chat/WhatsApp bot",
    "business": "AI chatbot + AI features on your existing site",
}
NICHE_EMOJI = {"health": "health", "commerce": "shop", "business": "business"}


# Caption phrases that suggest a business with NO website yet (best pitch target).
NO_WEB_RE = re.compile(
    r"(?:no website|don'?t have (?:a )?website|need(?:ed|s)? (?:a )?website|"
    r"link in (?:bio|our bio)|new (?:small )?business|just started|starting|"
    r"new (?:clinic|shop|store|brand)|small business|side hustle|startup)\b", re.I)

# Buying / hiring verbs that pair with a pitch about building presence.
DEMAND_RE = re.compile(
    r"\b(website|site|shop|store|page|dm|order|buy|browse|sell|need|want|help|"
    r"looking for|who can|available|open|call|appointment|book(?:ing)?|contact|"
    r"consult|visit)\b", re.I,
)


class InstagramScout(BaseStrategy):
    """Official Graph Hashtag Search -> leads + contact info, niche-tagged."""

    name = "instagram_scout"
    description = "Finds Instagram business leads with contact info (official API)."

    SEEN_KEY = "ig_seen_ids"
    DEFAULT_HASHTAGS = [
        "smallbusiness", "localbusiness", "clinic", "dentalclinic", "physiotherapy",
        "ayurveda", "skincareclinic", "hairsalon", "cafe", "boutique", "startup",
    ]
    BASE = "https://graph.facebook.com/v18.0"

    def run(self, ctx):
        tok = (self.opt("access_token") or "").strip()
        uid = (self.opt("user_id") or "").strip()
        if not tok or not uid:
            ctx.log.warning("[instagram_scout] Skipped: need access_token + user_id in config")  # noqa: E501
            return {"ok": False, "skipped": "no graph token/user_id"}

        hashtags = [h.strip().lstrip("#") for h in self.opt("hashtags", []) or [] if h.strip()]
        if not hashtags:
            hashtags = list(self.DEFAULT_HASHTAGS)
        keywords = [k.lower() for k in self.opt(
            "keywords", ["need", "appointment", "booking", "call", "website", "dm"]) or []]
        max_leads = int(self.opt("max_leads_per_run", 5))
        require_contact = bool(self.opt("require_contact", True))
        seen = set(ctx.ledger.get_state(self.SEEN_KEY, []) or [])

        posts, scanned = [], 0
        for htag in hashtags[:8]:
            try:
                hid = self._hashtag_name(tok, uid, htag)
                scanned += 1
                if hid:
                    posts.extend(self._recent_media(tok, uid, hid, keywords))
            except Exception as e:  # noqa: BLE001
                ctx.log.warning("[instagram_scout] hashtag %s failed: %s", htag, e)

        # dedupe + score + contact/niche
        unique, uniq_ids = [], set()
        for p in posts:
            pid = str(p.get("id") or p.get("permalink") or p.get("timestamp") or "")
            if not pid or pid in seen or pid in uniq_ids:
                continue
            uniq_ids.add(pid)
            cap = (p.get("caption") or "").strip()
            if not cap:
                continue
            phone, email = extract_contact(cap)
            niche = detect_niche(cap)
            hits = sorted({k for k in keywords if k in cap.lower()})
            nowb = bool(NO_WEB_RE.search(cap))
            has_contact = bool(phone or email)
            score = (len(hits) * 10
                     + (35 if nowb else 0)
                     + (20 if has_contact else 0)
                     + min(int(p.get("like_count") or 0), 30) // 3
                     + min(int(p.get("comments_count") or 0), 6))
            unique.append({**p, "hits": hits, "no_website": nowb,
                           "phone": phone, "email": email, "niche": niche,
                           "caption": clip(cap, 260), "has_contact": has_contact,
                           "score": round(score, 1), "id": pid})

        # only contact-rich leads make the notification when requested
        pool = [u for u in unique if (u["phone"] or u["email"])] if require_contact else unique
        pool.sort(key=lambda x: -x["score"])
        top = pool[:max_leads]
        leads = []
        for post in top:
            fname = f"ig-{slugify(post.get('username') or 'post', 24)}-{slugify(post['id'], 12)}.md"
            path = os.path.join(ctx.out_gigs, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._pitch(post))
            ctx.ledger.record(
                "instagram_scout", "lead", 0,
                note=f"@{post.get('username') or '?'} [{post['niche']}] "
                     f"phone={post.get('phone') or ''} email={post.get('email') or ''} | {post.get('permalink') or ''}",  # noqa: E501
            )
            leads.append({
                "username": post.get("username"),
                "niche": post["niche"],
                "phone": post.get("phone"),
                "email": post.get("email"),
                "no_website": post["no_website"],
                "url": post.get("permalink"),
                "score": post["score"],
                "file": fname,
            })

        seen |= {p["id"] for p in posts if isinstance(p, dict) and p.get("id")}
        ctx.ledger.set_state(self.SEEN_KEY, sorted(seen)[-800:])
        if top:
            lines = [f"📱 {len(top)} lead(s) + contact — call/WhatsApp/email now"]
            for p in top:
                niche = p["niche"]
                tag = f"{niche.upper()} · @{p.get('username') or '?'}"
                if p["no_website"]:
                    tag += " · NO WEBSITE"
                lines.append(
                    f"\n• {tag}\n"
                    f"  📞 {p.get('phone') or '—'} · ✉️ {p.get('email') or '—'}\n"
                    f"  💡 {OFFERS[niche]}\n"
                    f"  🔗 {p.get('permalink') or '#'}"
                )
            ctx.notify("\n".join(lines))
        return {"ok": True, "scanned": scanned, "new_matches": len(leads),
                "leads": leads, "checked_at": now_iso()}


    # ---- Graph endpoints (overridable/mockable in tests) ----
    def _hashtag_name(self, tok, uid, htag):
        url = f"{self.BASE}/{uid}/ig_hashtag_search?q={htag}"
        data = http_get_json(url + f"&access_token={tok}")
        hashes = (data or {}).get("data") or []
        for h in hashes:
            if str(h.get("name") or "").lstrip("#").lower() == htag.lower():
                return h.get("id")
        return None

    def _recent_media(self, tok, uid, htag_id, keywords):
        url = (f"{self.BASE}/{htag_id}/recent_media"
               f"?user_id={uid}&fields=caption,permalink,media_type,username,"
               f"timestamp,like_count,comments_count&limit=25&access_token={tok}")
        data = http_get_json(url)
        out = []
        for it in (data or {}).get("data") or []:
            cap = (it.get("caption") or "")
            if cap and DEMAND_RE.search(cap):
                out.append({
                    "id": str(it.get("id") or ""),
                    "caption": cap,
                    "permalink": it.get("permalink") or "",
                    "username": it.get("username") or "",
                    "media_type": it.get("media_type") or "",
                    "timestamp": it.get("timestamp") or "",
                    "like_count": int(it.get("like_count") or 0),
                    "comments_count": int(it.get("comments_count") or 0),
                })
        return out

    def _pitch(self, p):
        niche = p.get("niche") or detect_niche(p.get("caption") or "")
        offer = OFFERS[niche]
        no_site = ("I also noticed you may not have a website yet - I can get you "
                   "online fast." if p["no_website"] else "")
        contact = []
        if p.get("phone"):
            contact.append(f"Call/WhatsApp: {p['phone']}")
        if p.get("email"):
            contact.append(f"Email: {p['email']}")
        contact_s = " · ".join(contact)
        niche_upper = niche.upper()
        return ("# {niche_upper} lead - @{un}\n"
                "Niche      : {niche}\n"
                "Contact    : {c}\n"
                "Post       : {pl}\n"
                "Signal     : {sig}\n"
                "---\n"
                "Hi @{un}, I saw your recent post and love what you're doing.\n\n"
                "I build {offer} - exactly for businesses like yours.\n"
                "{ns}\n\n"
                "No long contracts, quick turnaround. Want a quick demo?\n"
                "I can reach you on WhatsApp/email - just reply.\n\n"
                "Best,\n(Your name)\n").format(
                    niche_upper=niche_upper, niche=niche_upper, un=p["username"],
                    c=contact_s or "n/a",
                    pl=p.get("permalink") or "#",
                    sig=("no website detected - prime upgrade target" if p["no_website"]
                         else "active business profile"),
                    offer=offer, ns=no_site)