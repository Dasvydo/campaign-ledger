"""campaign_db - the thin typed client for the DoviLoop Teams campaign ledger.

Batches C (outreach), D (reels), E (ads) and F (n8n) all write through this
module. It exposes eleven functions and nothing else:

    upsert_company          upsert_contact          log_touch
    record_reply            insert_lead             upsert_content
    snapshot_content_stats  snapshot_ad_stats
    get_market_funnel       get_channel_funnel      get_content_perf

Design rules this file keeps to
-------------------------------
*Idempotent on natural keys.* The outreach engine re-runs. Running it twice must
not produce two rows or move a timestamp. Every write here targets a unique
constraint declared in `migrations/001_schema.sql`:

    companies       domain
    contacts        (company_id, natural_key)
    touches         (contact_id, channel, sequence_step)
    leads           natural_key = lower(work_email)|submitted_at
    content         natural_key = kind:lane:language:slug(hook)
    content_stats   (content_id, platform, captured_on)
    ad_stats        (campaign_name, ad_set_name, captured_on)

Facts that cannot change once true - a touch was sent, a form was submitted -
are inserted with `ignore-duplicates`, so a replay returns the original row
rather than re-dating it. Facts that get enriched over time - what we know about
a company, today's view count - are merged.

*Credentials come from the environment and are never logged.* `SUPABASE_URL` and
`SUPABASE_SERVICE_KEY`. Nothing is hardcoded, and `_redact()` scrubs the key out
of every error message this module raises.

*The product database is refused at the door.* See `_FORBIDDEN_PROJECT_REF`.

Transport
---------
Plain PostgREST over `urllib` from the standard library. No `supabase-py`
dependency, because this module gets vendored into four other repos and a
zero-dependency file is easier to keep working than a pinned one. The `campaign`
schema is reached with the `Accept-Profile` / `Content-Profile` headers, which
requires `campaign` to be listed under Settings -> API -> Exposed schemas.
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import os as _os
import re as _re
import time as _time
import urllib.error as _urlerror
import urllib.parse as _urlparse
import urllib.request as _urlrequest
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "upsert_company",
    "upsert_contact",
    "log_touch",
    "record_reply",
    "insert_lead",
    "upsert_content",
    "snapshot_content_stats",
    "snapshot_ad_stats",
    "get_market_funnel",
    "get_channel_funnel",
    "get_content_perf",
]

SCHEMA = "campaign"

# The product database (Frankfurt). Never a target for anything in this repo.
# The build container's ambient SUPABASE_URL pointed here, which is exactly the
# accident this constant exists to stop. See AUDIT.md finding 1.
_FORBIDDEN_PROJECT_REF = "kngcxwcybozgqgnoweyt"

_TIMEOUT_SECONDS = 20
_RETRIES = 2  # on 5xx / network blips only, never on a 4xx


class CampaignDBError(RuntimeError):
    """Anything that went wrong talking to the ledger. Never carries the key."""


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------

_SLUG_RE = _re.compile(r"[^a-z0-9]+")


def _slug(text: str, max_len: int = 60) -> str:
    return _SLUG_RE.sub("-", (text or "").lower()).strip("-")[:max_len]


def _redact(text: str) -> str:
    """Remove the service key from anything on its way to a log or exception."""
    key = _os.environ.get("SUPABASE_SERVICE_KEY")
    if key and len(key) > 8:
        text = text.replace(key, "<SUPABASE_SERVICE_KEY redacted>")
    # Belt and braces: strip anything shaped like a JWT.
    return _re.sub(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+",
                   "<jwt redacted>", text)


def _iso(value: Any) -> str | None:
    """Normalise a datetime/date/str to an ISO-8601 string, or None."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.astimezone(_dt.timezone.utc).isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    return str(value)


def _day(value: Any) -> str:
    """Normalise to a YYYY-MM-DD date string."""
    if value is None:
        return _dt.date.today().isoformat()
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    return str(value)[:10]


def _clean(row: Mapping[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None so a merge never blanks a known column."""
    return {k: v for k, v in row.items() if v is not None}


def _one(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise CampaignDBError("write returned no row - check that the campaign "
                              "schema is in Settings -> API -> Exposed schemas")
    return dict(rows[0])


def _check(value: Any, allowed: Iterable[str], field: str) -> Any:
    """Fail in Python with a readable message rather than as a Postgres 22P02.

    Every one of these mirrors an enum in 001_schema.sql. A typo caught here
    names the field; the same typo caught by Postgres arrives as
    'invalid input value for enum'.
    """
    allowed = tuple(allowed)
    if value is not None and value not in allowed:
        raise CampaignDBError(
            f"{field}={value!r} is not one of {', '.join(allowed)}")
    return value


_MARKETS = ("dk", "lt", "global")
_LANGS = ("en", "da", "lt")
_SEGMENTS = ("accounting", "insurance", "admin")
_COMPANY_SOURCES = ("icp_finder", "linkedin", "inbound")
_EMAIL_SOURCES = ("instantly_finder", "public", "inbound")
_TOUCH_CHANNELS = ("linkedin_connect", "linkedin_dm", "email", "phone")
# The six-value reply taxonomy, canonical campaign-wide since 2026-09-06 and
# identical to what the outreach engine's classifier emits. Do not extend.
# The views read interested + referred as positive, not_now + objection as
# neutral, not_a_fit + unsubscribe as negative. All six are replies.
_SENTIMENTS = ("interested", "not_now", "not_a_fit", "referred", "objection",
               "unsubscribe")
_TEAM_SIZES = ("1-9", "10-24", "25-49", "50+")
_EMAIL_CLIENTS = ("outlook", "gmail", "other")
_LEAD_ROLES = ("owner_partner", "ops_office_manager", "it_admin", "other")
_LEAD_SOURCES = ("reel", "ad", "outreach", "direct")
_LEAD_STAGES = ("qualified", "gmail_on_request", "too_small")
_CONTENT_KINDS = ("reel", "static_ad", "video_ad")
_CONTENT_LANES = ("higgsfield", "hyperframes", "on_camera", "static")


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

class _PostgrestClient:
    """The smallest PostgREST client that covers the eleven functions."""

    def __init__(self, url: str, key: str) -> None:
        self._base = url.rstrip("/") + "/rest/v1"
        self._key = key

    @classmethod
    def from_env(cls) -> "_PostgrestClient":
        url = _os.environ.get("SUPABASE_URL", "").strip()
        key = _os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

        if not url or not key:
            missing = [n for n, v in
                       (("SUPABASE_URL", url), ("SUPABASE_SERVICE_KEY", key))
                       if not v]
            raise CampaignDBError(
                f"missing environment variable(s): {', '.join(missing)}. "
                "Copy .env.example and fill it in. Note the variable is "
                "SUPABASE_SERVICE_KEY, not SUPABASE_SERVICE_ROLE_KEY.")

        # Hard boundary. The product database is never a target, not even by
        # accident, not even if someone exports the wrong env file.
        if _FORBIDDEN_PROJECT_REF in url:
            raise CampaignDBError(
                "SUPABASE_URL points at the product database "
                f"({_FORBIDDEN_PROJECT_REF}). The campaign ledger lives in the "
                "lead-pipeline project, schema 'campaign'. Refusing to connect.")

        return cls(url, key)

    # -- request plumbing ---------------------------------------------------

    def _headers(self, *, write: bool, prefer: str | None) -> dict[str, str]:
        headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        # PostgREST reaches a non-public schema through these two.
        headers["Content-Profile" if write else "Accept-Profile"] = SCHEMA
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _request(self, method: str, path: str, *, params: Mapping[str, str] |
                 None = None, body: Any = None, prefer: str | None = None
                 ) -> list[dict[str, Any]]:
        url = f"{self._base}/{path}"
        if params:
            url += "?" + _urlparse.urlencode(params, doseq=True)
        data = _json.dumps(body).encode() if body is not None else None
        write = method in ("POST", "PATCH", "PUT", "DELETE")

        last_error: str = ""
        for attempt in range(_RETRIES + 1):
            req = _urlrequest.Request(
                url, data=data, method=method,
                headers=self._headers(write=write, prefer=prefer))
            try:
                with _urlrequest.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
                    raw = resp.read().decode() or "[]"
                    parsed = _json.loads(raw) if raw.strip() else []
                    return parsed if isinstance(parsed, list) else [parsed]
            except _urlerror.HTTPError as exc:
                detail = _redact(exc.read().decode(errors="replace")[:600])
                if 400 <= exc.code < 500:
                    # A 4xx is our fault. Retrying will not fix it.
                    raise CampaignDBError(
                        f"{method} {SCHEMA}.{path} -> HTTP {exc.code}: {detail}"
                    ) from None
                last_error = f"HTTP {exc.code}: {detail}"
            except (_urlerror.URLError, TimeoutError, OSError) as exc:
                last_error = _redact(str(exc))
            if attempt < _RETRIES:
                _time.sleep(0.6 * (attempt + 1))

        raise CampaignDBError(
            f"{method} {SCHEMA}.{path} failed after {_RETRIES + 1} attempts: "
            f"{last_error}")

    # -- the four operations the module needs -------------------------------

    def upsert(self, table: str, rows: Sequence[Mapping[str, Any]],
               on_conflict: str, *, ignore_duplicates: bool = False
               ) -> list[dict[str, Any]]:
        resolution = "ignore-duplicates" if ignore_duplicates else "merge-duplicates"
        return self._request(
            "POST", table,
            params={"on_conflict": on_conflict},
            body=[dict(r) for r in rows],
            prefer=f"resolution={resolution},return=representation")

    def update(self, table: str, values: Mapping[str, Any],
               eq: Mapping[str, Any]) -> list[dict[str, Any]]:
        params = {k: f"eq.{v}" for k, v in eq.items()}
        return self._request("PATCH", table, params=params, body=dict(values),
                             prefer="return=representation")

    def select(self, relation: str, *, eq: Mapping[str, Any] | None = None,
               order: str | None = None, limit: int | None = None
               ) -> list[dict[str, Any]]:
        params: dict[str, str] = {"select": "*"}
        for k, v in (eq or {}).items():
            params[k] = f"eq.{v}"
        if order:
            params["order"] = order
        if limit:
            params["limit"] = str(limit)
        return self._request("GET", relation, params=params)


_CLIENT: Any = None


def _client() -> Any:
    """The process-wide client, built on first use.

    Built lazily so importing this module never needs credentials - the seed
    script's local proof run swaps in a SQLite-backed stand-in by assigning to
    the module-level `_CLIENT` before the first call.
    """
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = _PostgrestClient.from_env()
    return _CLIENT


# ---------------------------------------------------------------------------
# Companies and contacts
# ---------------------------------------------------------------------------

def upsert_company(name: str, domain: str, market: str, *,
                   segment: str | None = None,
                   country: str | None = None,
                   est_size: int | None = None,
                   uses_m365: bool | None = None,
                   has_dev_team: bool | None = None,
                   fit_score: int | None = None,
                   hook_seed: str | None = None,
                   source: str = "icp_finder",
                   dsd_company_id: str | None = None) -> dict[str, Any]:
    """Create or enrich one target firm. Natural key: `domain`.

    The domain is lowercased and stripped of scheme, `www.` and any path, so
    "https://WWW.Example.dk/about" and "example.dk" are the same company. That
    normalisation is the whole reason this is idempotent - the outreach engine
    finds the same firm from three different sources and must not create three
    rows.

    Returns the stored row, including its `id`.
    """
    domain = _normalise_domain(domain)
    if not domain:
        raise CampaignDBError("domain is required - it is the natural key")

    _check(market, _MARKETS, "market")
    _check(segment, _SEGMENTS, "segment")
    _check(source, _COMPANY_SOURCES, "source")

    row = _clean({
        "name": name,
        "domain": domain,
        "market": market,
        "segment": segment,
        "country": country,
        "est_size": est_size,
        "uses_m365": uses_m365,
        "has_dev_team": has_dev_team,
        "fit_score": fit_score,
        "hook_seed": hook_seed,
        "source": source,
        "dsd_company_id": dsd_company_id,
    })
    return _one(_client().upsert("companies", [row], "domain"))


def _normalise_domain(domain: str | None) -> str:
    if not domain:
        return ""
    value = str(domain).strip().lower()
    value = _re.sub(r"^[a-z]+://", "", value)
    value = value.split("/")[0].split("?")[0]
    if "@" in value:                      # someone passed an email address
        value = value.rsplit("@", 1)[1]
    if value.startswith("www."):
        value = value[4:]
    return value


def upsert_contact(company_id: str, market: str, *,
                   full_name: str | None = None,
                   role_guess: str | None = None,
                   linkedin_url: str | None = None,
                   email: str | None = None,
                   email_source: str | None = None) -> dict[str, Any]:
    """Create or enrich one person at a firm.

    Natural key: `(company_id, natural_key)`, where natural_key is the LinkedIn
    URL if there is one, else the email, else the lowercased name. LinkedIn URL
    comes first because it is the identifier that survives a job change and a
    typo'd email guess.

    Returns the stored row, including its `id`.
    """
    _check(market, _MARKETS, "market")
    _check(email_source, _EMAIL_SOURCES, "email_source")

    linkedin_url = (linkedin_url or "").strip() or None
    email = (email or "").strip().lower() or None
    full_name = (full_name or "").strip() or None

    natural_key = (_normalise_linkedin(linkedin_url) or email
                   or (full_name.lower() if full_name else None))
    if not natural_key:
        raise CampaignDBError(
            "upsert_contact needs at least one of linkedin_url, email or "
            "full_name - there is nothing to de-duplicate on otherwise")

    row = _clean({
        "company_id": company_id,
        "full_name": full_name,
        "role_guess": role_guess,
        "linkedin_url": linkedin_url,
        "email": email,
        "email_source": email_source,
        "market": market,
        "natural_key": natural_key,
    })
    return _one(_client().upsert("contacts", [row], "company_id,natural_key"))


def _normalise_linkedin(url: str | None) -> str | None:
    if not url:
        return None
    value = str(url).strip().lower().split("?")[0].rstrip("/")
    value = _re.sub(r"^https?://", "", value)
    value = _re.sub(r"^([a-z]{2,3}\.)?linkedin\.com", "linkedin.com", value)
    return value or None


# ---------------------------------------------------------------------------
# Touches and replies
# ---------------------------------------------------------------------------

def log_touch(contact_id: str, channel: str, language: str, *,
              sequence_step: int = 1,
              sent_at: Any = None,
              notes: str | None = None) -> dict[str, Any]:
    """Record one outbound action. Natural key: `(contact_id, channel, step)`.

    Uses insert-if-absent rather than merge, on purpose. A touch is a fact about
    the past: if step 2 of the LinkedIn sequence already went out on Tuesday, a
    Thursday re-run of the engine must return Tuesday's row unchanged, not move
    `sent_at` forward. Re-dating it would quietly corrupt every reply-rate and
    time-to-reply number in the Friday brief.

    Sending policy is the outreach engine's job, not this table's. In
    particular: no Danish address may be given `channel='email'` until Dovy
    confirms the Danish marketing-law position. This function will happily
    record it - the engine must not call it.
    """
    _check(channel, _TOUCH_CHANNELS, "channel")
    _check(language, _LANGS, "language")
    if sequence_step < 1:
        raise CampaignDBError("sequence_step starts at 1")

    row = _clean({
        "contact_id": contact_id,
        "channel": channel,
        "sequence_step": sequence_step,
        "language": language,
        "sent_at": _iso(sent_at) or _iso(_dt.datetime.now(_dt.timezone.utc)),
        "notes": notes,
    })
    client = _client()
    stored = client.upsert("touches", [row], "contact_id,channel,sequence_step",
                           ignore_duplicates=True)
    if stored:
        return _one(stored)
    # Already logged. Return the row that is actually there.
    existing = client.select("touches", eq={
        "contact_id": contact_id,
        "channel": channel,
        "sequence_step": sequence_step,
    })
    return _one(existing)


def record_reply(contact_id: str, channel: str, sentiment: str, *,
                 sequence_step: int = 1,
                 replied_at: Any = None,
                 notes: str | None = None,
                 touch_id: str | None = None) -> dict[str, Any]:
    """Attach a reply and its classification to a touch that already exists.

    `sentiment` must be one of the six shared values:
    interested, not_now, not_a_fit, referred, objection, unsubscribe.
    Anything else is rejected here with a readable message rather than by
    Postgres as an enum cast error. `interested` and `referred` are what the
    funnel views count as positive replies; the other four are replies too,
    just not positive ones.

    Idempotent: recording the same reply twice writes the same values. Pass
    `touch_id` to target a specific row; otherwise the touch is found by
    `(contact_id, channel, sequence_step)`.
    """
    _check(sentiment, _SENTIMENTS, "sentiment")
    _check(channel, _TOUCH_CHANNELS, "channel")

    values = _clean({
        "replied_at": _iso(replied_at) or _iso(_dt.datetime.now(_dt.timezone.utc)),
        "reply_sentiment": sentiment,
        "notes": notes,
    })
    eq = ({"id": touch_id} if touch_id else
          {"contact_id": contact_id, "channel": channel,
           "sequence_step": sequence_step})

    updated = _client().update("touches", values, eq)
    if not updated:
        raise CampaignDBError(
            f"no touch to attach a reply to ({eq}). Call log_touch first - a "
            "reply without a send would break every reply-rate number.")
    return _one(updated)


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------

def _route_stage(team_size: str, email_client: str) -> str:
    """The routing table from 00-START-HERE.md, in one place.

    Private on purpose - Batch F owns routing, this is only here so
    insert_lead can default the stage consistently instead of trusting a
    caller-supplied string.
    """
    _check(team_size, _TEAM_SIZES, "team_size")
    _check(email_client, _EMAIL_CLIENTS, "email_client")
    if team_size == "1-9":
        return "too_small"
    return "qualified" if email_client == "outlook" else "gmail_on_request"


def insert_lead(payload: Mapping[str, Any], *,
                company_id: str | None = None,
                stage: str | None = None) -> dict[str, Any]:
    """Store one qualifier submission.

    `payload` is the shared contract object from 00-START-HERE.md, exactly as
    Batch A's form emits it and Batch F forwards it:

        {"source", "market", "locale", "utm": {"source", "medium", "campaign",
         "content"}, "company_name", "work_email", "phone", "team_size",
         "email_client", "role", "submitted_at"}

    `stage` is derived from `team_size` and `email_client` unless passed
    explicitly, so the ledger cannot disagree with the landing page about who
    was shown a booking link.

    Idempotent on `lower(work_email) + submitted_at`: an n8n retry of the same
    submission returns the original row, while a genuine second submission on a
    different day is a second lead, which is correct.
    """
    utm = payload.get("utm") or {}
    work_email = str(payload.get("work_email", "")).strip().lower()
    if not work_email:
        raise CampaignDBError("work_email is required")

    submitted_at = _iso(payload.get("submitted_at")) or _iso(
        _dt.datetime.now(_dt.timezone.utc))

    team_size = _check(payload.get("team_size"), _TEAM_SIZES, "team_size")
    email_client = _check(payload.get("email_client"), _EMAIL_CLIENTS,
                          "email_client")
    stage = stage or _route_stage(team_size, email_client)

    _check(stage, _LEAD_STAGES, "stage")
    _check(payload.get("market"), _MARKETS, "market")
    _check(payload.get("locale"), _LANGS, "locale")
    _check(payload.get("source"), _LEAD_SOURCES, "source")
    _check(payload.get("role"), _LEAD_ROLES, "role")

    row = _clean({
        "company_name": payload.get("company_name"),
        "work_email": work_email,
        "phone": payload.get("phone"),
        "team_size": team_size,
        "email_client": email_client,
        "role": payload.get("role"),
        "market": payload.get("market"),
        "locale": payload.get("locale"),
        "source": payload.get("source"),
        "utm_source": utm.get("source"),
        "utm_medium": utm.get("medium"),
        "utm_campaign": utm.get("campaign"),
        "utm_content": utm.get("content"),
        "stage": stage,
        "company_id": company_id,
        "submitted_at": submitted_at,
        "natural_key": f"{work_email}|{submitted_at}",
    })

    client = _client()
    stored = client.upsert("leads", [row], "natural_key", ignore_duplicates=True)
    if stored:
        return _one(stored)
    return _one(client.select("leads", eq={"natural_key": row["natural_key"]}))


# ---------------------------------------------------------------------------
# Content and its numbers
# ---------------------------------------------------------------------------

def upsert_content(kind: str, lane: str, language: str, hook: str, *,
                   script_path: str | None = None,
                   asset_path: str | None = None,
                   platforms: Sequence[str] | None = None,
                   published_at: Any = None,
                   buffer_id: str | None = None) -> dict[str, Any]:
    """Create or update one reel or ad creative.

    Natural key: `kind:lane:language:slug(hook)`. The reel engine regenerates a
    week's scripts on every run; keying on the hook means the same script keeps
    the same row and keeps its accumulated stats, instead of fanning out a new
    row that starts from zero views.

    `platforms` is where it went out: linkedin, youtube_shorts, instagram,
    facebook.
    """
    _check(kind, _CONTENT_KINDS, "kind")
    _check(lane, _CONTENT_LANES, "lane")
    _check(language, _LANGS, "language")
    if not hook or not hook.strip():
        raise CampaignDBError("hook is required - it is part of the natural key")

    row = _clean({
        "kind": kind,
        "lane": lane,
        "language": language,
        "hook": hook.strip(),
        "script_path": script_path,
        "asset_path": asset_path,
        "platforms": list(platforms) if platforms is not None else None,
        "published_at": _iso(published_at),
        "buffer_id": buffer_id,
        "natural_key": f"{kind}:{lane}:{language}:{_slug(hook)}",
    })
    return _one(_client().upsert("content", [row], "natural_key"))


def snapshot_content_stats(content_id: str, platform: str, captured_on: Any,
                           *, views: int = 0, likes: int = 0, comments: int = 0,
                           saves: int = 0, clicks: int = 0) -> dict[str, Any]:
    """Record one day's numbers for one content item on one platform.

    Natural key: `(content_id, platform, captured_on)`. Merged rather than
    ignored - a second capture on the same day is a better reading of that day,
    not a duplicate, so it overwrites.
    """
    row = {
        "content_id": content_id,
        "platform": platform,
        "captured_on": _day(captured_on),
        "views": int(views),
        "likes": int(likes),
        "comments": int(comments),
        "saves": int(saves),
        "clicks": int(clicks),
    }
    return _one(_client().upsert("content_stats", [row],
                                 "content_id,platform,captured_on"))


def snapshot_ad_stats(campaign_name: str, ad_set_name: str, captured_on: Any, *,
                      creative_content_id: str | None = None,
                      spend_eur: float = 0, impressions: int = 0,
                      clicks: int = 0, leads: int = 0) -> dict[str, Any]:
    """Record one day's numbers for one Meta ad set.

    Natural key: `(campaign_name, ad_set_name, captured_on)`. Merged, for the
    same reason as content stats.

    `creative_content_id` is optional because Meta reports at ad-set level and
    the link back to a specific creative is not always recoverable. Leave it
    None rather than guessing - a wrong link makes `v_content_perf` blame the
    wrong lane.
    """
    row = _clean({
        "campaign_name": campaign_name,
        "ad_set_name": ad_set_name,
        "creative_content_id": creative_content_id,
        "captured_on": _day(captured_on),
        "spend_eur": round(float(spend_eur), 2),
        "impressions": int(impressions),
        "clicks": int(clicks),
        "leads": int(leads),
    })
    return _one(_client().upsert("ad_stats", [row],
                                 "campaign_name,ad_set_name,captured_on"))


# ---------------------------------------------------------------------------
# Reads - the three views the Friday brief runs on
# ---------------------------------------------------------------------------

def get_market_funnel() -> list[dict[str, Any]]:
    """`campaign.v_market_funnel`: one row per market, dk / lt / global."""
    return _client().select("v_market_funnel", order="sort_order.asc")


def get_channel_funnel() -> list[dict[str, Any]]:
    """`campaign.v_channel_funnel`: one row per source, outreach / reel / ad / direct."""
    return _client().select("v_channel_funnel", order="sort_order.asc")


def get_content_perf(*, limit: int | None = None) -> list[dict[str, Any]]:
    """`campaign.v_content_perf`: latest snapshot per item per platform.

    Already ordered best-engagement-first by the view, with unmeasured items
    last.
    """
    return _client().select("v_content_perf", limit=limit)
