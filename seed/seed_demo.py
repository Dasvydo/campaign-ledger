#!/usr/bin/env python3
"""Fake but plausible rows, so the views and the Friday brief can be tested
before a single real lead exists.

    python3 seed/seed_demo.py --local     # in-memory SQLite mirror, no database
    python3 seed/seed_demo.py             # the real ledger, via SUPABASE_URL

Every value below is hardcoded and deterministic. There is no randomness and no
`now()` anywhere, because the seed doubles as the idempotency proof: run it
twice and every row must be byte-identical. A random name or a wall-clock
timestamp would make that test pass or fail by luck.

The data is shaped like the campaign actually is, not like a lorem generator:

* three markets with deliberately different performance, so the brief has
  something real to say. Lithuania converts, Denmark is slow, global is noisy.
* Denmark gets LinkedIn and phone touches only. **No Danish contact is given an
  email touch**, because Danish marketing law is stricter than the rest of the
  EU on unsolicited commercial email and Dovy has not confirmed the position
  yet. If a future change to the seed adds one, the assertion at the bottom of
  this file fails.
* all six reply-sentiment values (interested, not_now, not_a_fit, referred,
  objection, unsubscribe) appear at least once, so the taxonomy is exercised
  end to end rather than just declared, and both positive values are present
  so `positive_replies` in the views is tested against a non-zero number.
* content covers all three reel lanes plus both ad formats, with two snapshot
  days each, so `v_content_perf`'s "latest snapshot only" logic is actually
  under test rather than trivially satisfied.

WARNING: this writes ~60 rows. Do not run it against the live ledger once real
data is in. It is for a fresh schema, or the local mirror.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import campaign_db as db  # noqa: E402

# Campaign window opens 8 Sept 2026. Everything below sits inside it.
WEEK1 = "2026-09-08"
WEEK2 = "2026-09-15"
WEEK3 = "2026-09-22"


# ---------------------------------------------------------------------------
# 1. Companies - 4 per market, across the three ICP segments
# ---------------------------------------------------------------------------

COMPANIES = [
    # (name, domain, market, segment, country, est_size, m365, dev_team, fit, hook)
    ("Nordbro Revision",        "nordbrorevision.dk",  "dk", "accounting", "Denmark",  34, True,  False, 88, "hiring a third bookkeeper this autumn"),
    ("Jyllands Forsikring",     "jyllandsforsikring.dk", "dk", "insurance", "Denmark", 61, True,  False, 82, "moved claims intake to a shared inbox"),
    ("Kollegiet Administration", "kollegieadm.dk",     "dk", "admin",      "Denmark",  22, True,  False, 79, "runs 9 student housing sites on spreadsheets"),
    ("Aarhus Ejendomsdrift",    "aarhusejendom.dk",    "dk", "admin",      "Denmark",  15, False, False, 54, "posted about invoice backlogs"),

    ("Vilniaus Apskaita",       "vilniausapskaita.lt", "lt", "accounting", "Lithuania", 41, True,  False, 91, "doubled client count without new hires"),
    ("Baltijos Draudimas",      "baltijosdraudimas.lt", "lt", "insurance", "Lithuania", 78, True,  False, 86, "manual policy renewals mentioned in a job ad"),
    ("Kauno Administravimas",   "kaunoadmin.lt",       "lt", "admin",      "Lithuania", 26, True,  False, 84, "manages 400 rental units"),
    ("Klaipeda Turto Valdymas", "klaipedaturtas.lt",   "lt", "admin",      "Lithuania", 12, True,  True,  38, "has two developers in house"),

    ("Harbor Point Accounting", "harborpointcpa.com",  "global", "accounting", "United States", 55, True,  False, 87, "tax season overtime post"),
    ("Meridian Risk Partners",  "meridianrisk.com",    "global", "insurance",  "United States", 93, True,  False, 80, "broker onboarding takes six weeks"),
    ("Cedar Facilities Group",  "cedarfacilities.com", "global", "admin",      "Canada",        38, True,  False, 76, "facility work orders by email"),
    ("Stonebridge Bookkeeping", "stonebridgebooks.com", "global", "accounting", "United Kingdom", 11, False, False, 44, "small practice, Google Workspace"),
]


# ---------------------------------------------------------------------------
# 2. Contacts - one decision maker per firm, two at the biggest
# ---------------------------------------------------------------------------

CONTACTS = [
    # (domain, full_name, role_guess, linkedin_slug, email, email_source)
    ("nordbrorevision.dk",   "Mette Sorensen",  "Partner",          "mette-sorensen-nb",  None,                              None),
    ("jyllandsforsikring.dk", "Lars Bech",      "Head of Claims",   "lars-bech-jf",       None,                              None),
    ("kollegieadm.dk",       "Anne Kirkegaard", "Office Manager",   "anne-kirkegaard-ka", None,                              None),
    ("aarhusejendom.dk",     "Peter Holm",      "Owner",            "peter-holm-ae",      None,                              None),

    ("vilniausapskaita.lt",  "Ruta Kazlauskiene", "Direktore",      "ruta-kazlauskiene",  "ruta@vilniausapskaita.lt",        "instantly_finder"),
    ("baltijosdraudimas.lt", "Tomas Petraitis",   "Operaciju vadovas", "tomas-petraitis", "t.petraitis@baltijosdraudimas.lt", "instantly_finder"),
    ("baltijosdraudimas.lt", "Gabija Norkute",    "IT administratore", "gabija-norkute",  None,                              None),
    ("kaunoadmin.lt",        "Darius Jankauskas", "Savininkas",     "darius-jankauskas",  "darius@kaunoadmin.lt",            "public"),
    ("klaipedaturtas.lt",    "Egle Simkute",      "Vadove",         "egle-simkute",       None,                              None),

    ("harborpointcpa.com",   "Dana Whitfield",  "Managing Partner", "dana-whitfield-cpa", "dana@harborpointcpa.com",         "instantly_finder"),
    ("meridianrisk.com",     "Marcus Reyes",    "Director of Ops",  "marcus-reyes-mrp",   "m.reyes@meridianrisk.com",        "instantly_finder"),
    ("cedarfacilities.com",  "Priya Raman",     "Facilities Lead",  "priya-raman-cfg",    "priya@cedarfacilities.com",       "public"),
    ("stonebridgebooks.com", "Alan Fenwick",    "Owner",            "alan-fenwick-sbb",   "alan@stonebridgebooks.com",       "public"),
]


# ---------------------------------------------------------------------------
# 3. Touches
#
# (contact linkedin_slug, channel, step, language, sent_at)
# Denmark: linkedin_connect, linkedin_dm, phone. Never email.
# ---------------------------------------------------------------------------

TOUCHES = [
    ("mette-sorensen-nb",  "linkedin_connect", 1, "da", f"{WEEK1}T09:10:00+00:00"),
    ("mette-sorensen-nb",  "linkedin_dm",      2, "da", f"{WEEK2}T09:12:00+00:00"),
    ("lars-bech-jf",       "linkedin_connect", 1, "da", f"{WEEK1}T09:20:00+00:00"),
    ("lars-bech-jf",       "linkedin_dm",      2, "da", f"{WEEK2}T09:22:00+00:00"),
    ("lars-bech-jf",       "phone",            3, "da", f"{WEEK3}T10:05:00+00:00"),
    ("anne-kirkegaard-ka", "linkedin_connect", 1, "da", f"{WEEK1}T09:30:00+00:00"),
    ("anne-kirkegaard-ka", "linkedin_dm",      2, "da", f"{WEEK2}T09:31:00+00:00"),
    ("peter-holm-ae",      "linkedin_connect", 1, "da", f"{WEEK1}T09:40:00+00:00"),

    ("ruta-kazlauskiene",  "linkedin_connect", 1, "lt", f"{WEEK1}T08:05:00+00:00"),
    ("ruta-kazlauskiene",  "linkedin_dm",      2, "lt", f"{WEEK2}T08:06:00+00:00"),
    ("ruta-kazlauskiene",  "email",            3, "lt", f"{WEEK3}T08:00:00+00:00"),
    ("tomas-petraitis",    "linkedin_connect", 1, "lt", f"{WEEK1}T08:15:00+00:00"),
    ("tomas-petraitis",    "email",            2, "lt", f"{WEEK2}T08:16:00+00:00"),
    ("gabija-norkute",     "linkedin_connect", 1, "lt", f"{WEEK1}T08:25:00+00:00"),
    ("darius-jankauskas",  "linkedin_connect", 1, "lt", f"{WEEK1}T08:35:00+00:00"),
    ("darius-jankauskas",  "email",            2, "lt", f"{WEEK2}T08:36:00+00:00"),
    ("egle-simkute",       "linkedin_connect", 1, "lt", f"{WEEK1}T08:45:00+00:00"),

    ("dana-whitfield-cpa", "linkedin_connect", 1, "en", f"{WEEK1}T14:05:00+00:00"),
    ("dana-whitfield-cpa", "email",            2, "en", f"{WEEK2}T14:06:00+00:00"),
    ("marcus-reyes-mrp",   "linkedin_connect", 1, "en", f"{WEEK1}T14:15:00+00:00"),
    ("marcus-reyes-mrp",   "email",            2, "en", f"{WEEK2}T14:16:00+00:00"),
    ("marcus-reyes-mrp",   "linkedin_dm",      3, "en", f"{WEEK3}T14:20:00+00:00"),
    ("priya-raman-cfg",    "linkedin_connect", 1, "en", f"{WEEK1}T14:25:00+00:00"),
    ("priya-raman-cfg",    "email",            2, "en", f"{WEEK2}T14:26:00+00:00"),
    ("alan-fenwick-sbb",   "email",            1, "en", f"{WEEK1}T14:35:00+00:00"),
]

# All six taxonomy values are used, in a distribution shaped like a real
# week of cold outreach: a few interested, a few polite deferrals, some
# pushback, and one each of the rarer outcomes. Ten replies:
#   interested x3, not_now x2, objection x2, not_a_fit x1, referred x1,
#   unsubscribe x1.
# The three interested contacts are the ones who go on to book calls below.
# Egle's firm has an in-house dev team (not_a_fit); Alan's is an 11-seat
# Google Workspace shop that asked to be left alone (unsubscribe); Priya
# passed the note to a colleague (referred) and later came in direct.
# (slug, channel, step, sentiment, replied_at)
REPLIES = [
    ("mette-sorensen-nb",  "linkedin_dm",      2, "not_now",     f"{WEEK2}T15:00:00+00:00"),
    ("lars-bech-jf",       "phone",            3, "objection",   f"{WEEK3}T10:20:00+00:00"),
    ("ruta-kazlauskiene",  "linkedin_dm",      2, "interested",  f"{WEEK2}T11:00:00+00:00"),
    ("tomas-petraitis",    "email",            2, "not_now",     f"{WEEK2}T13:00:00+00:00"),
    ("darius-jankauskas",  "email",            2, "interested",  f"{WEEK2}T16:30:00+00:00"),
    ("egle-simkute",       "linkedin_connect", 1, "not_a_fit",   f"{WEEK1}T17:00:00+00:00"),
    ("dana-whitfield-cpa", "email",            2, "interested",  f"{WEEK2}T18:00:00+00:00"),
    ("marcus-reyes-mrp",   "email",            2, "objection",   f"{WEEK2}T19:00:00+00:00"),
    ("priya-raman-cfg",    "email",            2, "referred",    f"{WEEK2}T20:00:00+00:00"),
    ("alan-fenwick-sbb",   "email",            1, "unsubscribe", f"{WEEK1}T21:00:00+00:00"),
]


# ---------------------------------------------------------------------------
# 4. Leads - the qualifier payload, exactly the shape from 00-START-HERE.md
# ---------------------------------------------------------------------------

def _lead(source, market, locale, company, email, phone, size, client, role,
          submitted, utm):
    return {
        "source": source,
        "market": market,
        "locale": locale,
        "utm": utm,
        "company_name": company,
        "work_email": email,
        "phone": phone,
        "team_size": size,
        "email_client": client,
        "role": role,
        "submitted_at": submitted,
    }


LEADS = [
    # Lithuania: outreach works here.
    _lead("outreach", "lt", "lt", "Vilniaus Apskaita", "ruta@vilniausapskaita.lt",
          "+370 600 11111", "25-49", "outlook", "owner_partner",
          f"{WEEK2}T11:40:00+00:00",
          {"source": "linkedin", "medium": "dm", "campaign": "lt-outreach-w1", "content": "hook-a"}),
    _lead("outreach", "lt", "lt", "Kauno Administravimas", "darius@kaunoadmin.lt",
          "+370 600 22222", "25-49", "outlook", "owner_partner",
          f"{WEEK2}T17:05:00+00:00",
          {"source": "linkedin", "medium": "email", "campaign": "lt-outreach-w1", "content": "hook-b"}),
    _lead("reel", "lt", "lt", "Panevezio Buhalterija", "info@panevezio-buh.lt",
          "+370 600 33333", "10-24", "gmail", "ops_office_manager",
          f"{WEEK3}T09:15:00+00:00",
          {"source": "linkedin", "medium": "organic", "campaign": "reel-w2", "content": "hyperframes-lt"}),

    # Denmark: reach without conversion so far.
    _lead("outreach", "dk", "da", "Nordbro Revision", "mette@nordbrorevision.dk",
          "+45 20 11 11 11", "25-49", "outlook", "owner_partner",
          f"{WEEK2}T15:30:00+00:00",
          {"source": "linkedin", "medium": "dm", "campaign": "dk-outreach-w1", "content": "hook-a"}),
    _lead("reel", "dk", "da", "Odense Boligadministration", "kontakt@odenseboligadm.dk",
          "+45 20 22 22 22", "10-24", "other", "ops_office_manager",
          f"{WEEK3}T12:00:00+00:00",
          {"source": "linkedin", "medium": "organic", "campaign": "reel-w2", "content": "oncamera-da"}),

    # Global: ads bring volume, including the wrong size.
    _lead("ad", "global", "en", "Harbor Point Accounting", "dana@harborpointcpa.com",
          "+1 415 555 0101", "50+", "outlook", "owner_partner",
          f"{WEEK2}T18:20:00+00:00",
          {"source": "meta", "medium": "retargeting", "campaign": "teams-retarget", "content": "static-roi"}),
    _lead("ad", "global", "en", "Lakeside Bookkeeping", "hello@lakesidebooks.com",
          None, "1-9", "gmail", "owner_partner",
          f"{WEEK2}T19:45:00+00:00",
          {"source": "meta", "medium": "retargeting", "campaign": "teams-retarget", "content": "video-demo"}),
    _lead("ad", "global", "en", "Trellis Property Admin", "ops@trellisadmin.com",
          "+1 312 555 0144", "10-24", "outlook", "ops_office_manager",
          f"{WEEK3}T16:10:00+00:00",
          {"source": "meta", "medium": "retargeting", "campaign": "teams-retarget", "content": "static-roi"}),
    _lead("direct", "global", "en", "Cedar Facilities Group", "priya@cedarfacilities.com",
          "+1 604 555 0177", "25-49", "gmail", "it_admin",
          f"{WEEK3}T20:00:00+00:00",
          {"source": "direct", "medium": "none", "campaign": "", "content": ""}),
    _lead("ad", "global", "en", "Quill & Ledger", "team@quillledger.com",
          None, "1-9", "other", "other",
          f"{WEEK3}T21:30:00+00:00",
          {"source": "meta", "medium": "retargeting", "campaign": "teams-retarget", "content": "video-demo"}),
]

# Which lead (by work_email) is linked to which already-known company.
LEAD_COMPANY_LINK = {
    "ruta@vilniausapskaita.lt": "vilniausapskaita.lt",
    "darius@kaunoadmin.lt": "kaunoadmin.lt",
    "mette@nordbrorevision.dk": "nordbrorevision.dk",
    "dana@harborpointcpa.com": "harborpointcpa.com",
    "priya@cedarfacilities.com": "cedarfacilities.com",
}


# ---------------------------------------------------------------------------
# 5. Meetings and pilots
# ---------------------------------------------------------------------------

MEETINGS = [
    # (lead work_email, scheduled_for, status, pilot_agreed, notes)
    ("ruta@vilniausapskaita.lt", f"{WEEK3}T10:00:00+00:00", "held",    True,
     "41 seats. Month-end close is the pain. Wants the workshop before 1 Oct."),
    ("darius@kaunoadmin.lt",     f"{WEEK3}T14:00:00+00:00", "held",    True,
     "26 seats across two offices. Asked about Outlook rules migration."),
    ("mette@nordbrorevision.dk", f"{WEEK3}T11:00:00+00:00", "no_show", False,
     "No show. Rebooked once, no reply to the second invite."),
    ("dana@harborpointcpa.com",  "2026-09-29T16:00:00+00:00", "booked", False,
     "Booked from the retargeting ad. 55 seats."),
    ("ops@trellisadmin.com",     "2026-09-30T15:00:00+00:00", "booked", False,
     "10-24 seats, Outlook. Wants pricing in writing first."),
    ("priya@cedarfacilities.com", f"{WEEK3}T21:00:00+00:00", "cancelled", False,
     "Cancelled the morning of. Gmail shop, was told the Gmail path is on request."),
]

PILOTS = [
    # (lead work_email, started_on, workshop_done_on, setup_done_on, seats,
    #  converted, stripe_customer_id, mrr_eur, lost_reason)
    ("ruta@vilniausapskaita.lt", "2026-09-24", "2026-09-25", "2026-09-26", 41,
     True,  "cus_demo_vilnius", 3197.00, None),
    ("darius@kaunoadmin.lt",     "2026-09-25", "2026-09-26", None,        26,
     False, None,               None,    None),
]


# ---------------------------------------------------------------------------
# 6. Content and its numbers
# ---------------------------------------------------------------------------

CONTENT = [
    # (kind, lane, language, hook, script_path, platforms, published_at)
    ("reel", "higgsfield", "en",
     "Your team already has the data. It is sat in 400 unread emails.",
     "scripts/w1-higgsfield-en.md",
     ["linkedin", "youtube_shorts", "instagram", "facebook"], f"{WEEK1}T07:00:00+00:00"),
    ("reel", "hyperframes", "en",
     "Ten people, one shared inbox, zero idea who answered what.",
     "scripts/w1-hyperframes-en.md",
     ["linkedin", "youtube_shorts", "instagram", "facebook"], f"{WEEK1}T07:05:00+00:00"),
    ("reel", "on_camera", "en",
     "We saved a 30 person firm about 400 euro a month. Here is the boring part.",
     "scripts/w1-oncamera-en.md",
     ["linkedin", "youtube_shorts", "instagram", "facebook"], f"{WEEK1}T07:10:00+00:00"),
    ("reel", "hyperframes", "lt",
     "Desimt zmoniu, viena bendra pasto dezute, ir niekas nezino kas atsake.",
     "scripts/w1-hyperframes-lt.md", ["linkedin"], f"{WEEK1}T07:20:00+00:00"),
    ("reel", "on_camera", "da",
     "Vi sparede et firma med 30 ansatte omkring 400 euro om maaneden.",
     "scripts/w1-oncamera-da.md", ["linkedin"], f"{WEEK1}T07:25:00+00:00"),

    ("static_ad", "static", "en",
     "9x return. 40 day payback. No developers needed.",
     "ads/static-roi.md", ["facebook", "instagram"], f"{WEEK2}T06:00:00+00:00"),
    ("video_ad", "higgsfield", "en",
     "The demo, in 20 seconds.",
     "ads/video-demo.md", ["facebook", "instagram"], f"{WEEK2}T06:05:00+00:00"),
]

# (content natural-key hook fragment, platform, captured_on, views, likes,
#  comments, saves, clicks). Two capture days each, so v_content_perf has to
# pick the later one.
CONTENT_STATS = [
    ("Your team already has",  "linkedin",       WEEK2, 1180,  22,  3,  6, 14),
    ("Your team already has",  "linkedin",       WEEK3, 1640,  31,  4,  9, 21),
    ("Your team already has",  "youtube_shorts", WEEK3, 2410,  18,  1,  2,  5),
    ("Ten people, one shared", "linkedin",       WEEK2, 2050,  74, 11, 19, 38),
    ("Ten people, one shared", "linkedin",       WEEK3, 3120, 118, 17, 31, 62),
    ("Ten people, one shared", "instagram",      WEEK3, 4400,  96,  6, 12, 11),
    ("We saved a 30 person",   "linkedin",       WEEK2, 1490,  61,  9, 14, 33),
    ("We saved a 30 person",   "linkedin",       WEEK3, 2260,  99, 15, 24, 55),
    ("Desimt zmoniu",          "linkedin",       WEEK3,  540,  27,  5,  8, 12),
    ("Vi sparede et firma",    "linkedin",       WEEK3,  610,   9,  0,  1,  3),
    ("9x return",              "facebook",       WEEK3, 8800,  41,  2,  3, 96),
    ("The demo, in 20",        "facebook",       WEEK3, 6300,  28,  1,  2, 44),
]

AD_STATS = [
    # (campaign_name, ad_set_name, creative hook fragment, captured_on,
    #  spend_eur, impressions, clicks, leads)
    ("teams-retarget", "static-roi",  "9x return",       WEEK2,  38.40, 4200, 51,  1),
    ("teams-retarget", "static-roi",  "9x return",       WEEK3,  46.10, 4600, 45,  1),
    ("teams-retarget", "video-demo",  "The demo, in 20", WEEK2,  41.75, 3300, 26,  1),
    ("teams-retarget", "video-demo",  "The demo, in 20", WEEK3,  52.30, 3000, 18,  1),
]


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def seed(verbose: bool = True) -> dict[str, int]:
    """Write every row. Safe to run repeatedly - the second run is a no-op."""
    counts: dict[str, int] = {}

    companies: dict[str, str] = {}
    for (name, domain, market, segment, country, size, m365, dev, fit,
         hook) in COMPANIES:
        row = db.upsert_company(
            name, domain, market, segment=segment, country=country,
            est_size=size, uses_m365=m365, has_dev_team=dev, fit_score=fit,
            hook_seed=hook, source="icp_finder")
        companies[domain] = row["id"]
    counts["companies"] = len(companies)

    contacts: dict[str, str] = {}
    for domain, full_name, role, slug, email, email_source in CONTACTS:
        market = next(c[2] for c in COMPANIES if c[1] == domain)
        row = db.upsert_contact(
            companies[domain], market, full_name=full_name, role_guess=role,
            linkedin_url=f"https://www.linkedin.com/in/{slug}",
            email=email, email_source=email_source)
        contacts[slug] = row["id"]
    counts["contacts"] = len(contacts)

    for slug, channel, step, language, sent_at in TOUCHES:
        db.log_touch(contacts[slug], channel, language,
                     sequence_step=step, sent_at=sent_at)
    counts["touches"] = len(TOUCHES)

    for slug, channel, step, sentiment, replied_at in REPLIES:
        db.record_reply(contacts[slug], channel, sentiment,
                        sequence_step=step, replied_at=replied_at)
    counts["replies"] = len(REPLIES)

    leads: dict[str, str] = {}
    for payload in LEADS:
        linked_domain = LEAD_COMPANY_LINK.get(payload["work_email"])
        row = db.insert_lead(
            payload,
            company_id=companies.get(linked_domain) if linked_domain else None)
        leads[payload["work_email"]] = row["id"]
    counts["leads"] = len(leads)

    # meetings and pilots have no public client function - the eleven exported
    # by campaign_db are the ones the other batches need, and neither engine
    # writes a meeting. The seed reaches the private client directly rather
    # than widening the public surface for test data's sake.
    client = db._client()
    for email, scheduled_for, status, agreed, notes in MEETINGS:
        client.upsert("meetings", [{
            "lead_id": leads[email],
            "scheduled_for": scheduled_for,
            "status": status,
            "pilot_agreed": agreed,
            "outcome_notes": notes,
        }], "lead_id,scheduled_for")
    counts["meetings"] = len(MEETINGS)

    for (email, started, workshop, setup, seats, converted, stripe_id, mrr,
         lost) in PILOTS:
        row = {
            "lead_id": leads[email],
            "started_on": started,
            "workshop_done_on": workshop,
            "setup_done_on": setup,
            "seats": seats,
            "converted": converted,
            "stripe_customer_id": stripe_id,
            "mrr_eur": mrr,
            "lost_reason": lost,
        }
        client.upsert("pilots", [{k: v for k, v in row.items() if v is not None}],
                      "lead_id")
    counts["pilots"] = len(PILOTS)

    content: dict[str, str] = {}
    for kind, lane, language, hook, script_path, platforms, published in CONTENT:
        row = db.upsert_content(
            kind, lane, language, hook, script_path=script_path,
            platforms=platforms, published_at=published)
        content[hook] = row["id"]
    counts["content"] = len(content)

    def content_id(fragment: str) -> str:
        return content[next(h for h in content if h.startswith(fragment))]

    for fragment, platform, day, views, likes, comments, saves, clicks in CONTENT_STATS:
        db.snapshot_content_stats(
            content_id(fragment), platform, day, views=views, likes=likes,
            comments=comments, saves=saves, clicks=clicks)
    counts["content_stats"] = len(CONTENT_STATS)

    for name, ad_set, fragment, day, spend, impressions, clicks, lead_count in AD_STATS:
        db.snapshot_ad_stats(
            name, ad_set, day, creative_content_id=content_id(fragment),
            spend_eur=spend, impressions=impressions, clicks=clicks,
            leads=lead_count)
    counts["ad_stats"] = len(AD_STATS)

    if verbose:
        for table, n in counts.items():
            print(f"  {table:<15} {n}")
    return counts


def _assert_no_danish_email() -> None:
    """The Danish no-cold-email rule, enforced on the seed data itself.

    Denmark's marketing law is stricter than the rest of the EU on unsolicited
    commercial email, and Dovy has not confirmed the position. Until he does, no
    Danish contact gets an email touch - not even in demo data that someone
    might later copy as a template.
    """
    danish_slugs = {
        slug for domain, _, _, slug, _, _ in CONTACTS
        if next(c[2] for c in COMPANIES if c[1] == domain) == "dk"
    }
    offenders = [(slug, channel) for slug, channel, *_ in TOUCHES
                 if channel == "email" and slug in danish_slugs]
    if offenders:
        raise AssertionError(
            f"Danish contacts given an email touch: {offenders}. "
            "No cold email to Denmark until Dovy confirms the legal position.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--local", action="store_true",
        help="run against an in-memory SQLite mirror of the migrations "
             "instead of a real database")
    args = parser.parse_args(argv)

    _assert_no_danish_email()

    if args.local:
        sys.path.insert(
            0, str(pathlib.Path(__file__).resolve().parent.parent / "tests"))
        import sqlite_mirror
        sqlite_mirror.install()
        print("seeding the local SQLite mirror (no database connection)")
    else:
        print("seeding the live ledger from SUPABASE_URL")

    seed()
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
