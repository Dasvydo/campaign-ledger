#!/usr/bin/env python3
"""One page of markdown, every Friday, from the three ledger views.

    python3 src/friday_brief.py                 # live ledger
    python3 src/friday_brief.py --local         # SQLite mirror + seed data
    python3 src/friday_brief.py --no-write      # stdout only

Writes `briefs/YYYY-MM-DD.md` and prints the same text to stdout, so n8n can
either read the file or pipe the output straight into an email node.

On the tone of the last section
-------------------------------
"What the numbers suggest" is written to suggest, not to conclude. Every line in
it carries the figures that produced it, and any line resting on a denominator
under `THIN_EVIDENCE` is marked as thin. The brief then ends with the raw
numbers rather than a recommendation, because in week two of a six week campaign
the honest answer to "which market wins" is usually "not enough data yet", and a
brief that hides that behind a confident sentence is worse than no brief.
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import campaign_db as db  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Below this many touches / leads / views, a comparison is noise. Two markets
# separated by one booked call out of six is not a finding.
THIN_EVIDENCE = 30

MARKET_LABEL = {"dk": "Denmark", "lt": "Lithuania", "global": "US / global"}
CHANNEL_LABEL = {"outreach": "Outreach", "reel": "Reels", "ad": "Meta ads",
                 "direct": "Direct"}
LANE_LABEL = {"higgsfield": "Higgsfield", "hyperframes": "HyperFrames",
              "on_camera": "Dovy on camera", "static": "Static ad"}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _n(value: Any) -> str:
    """A number, or a dash. Never a fabricated zero."""
    if value is None:
        return "-"
    if isinstance(value, float) and value == int(value):
        value = int(value)
    return f"{value:,}" if isinstance(value, int) else str(value)


def _pct(value: Any) -> str:
    return "-" if value is None else f"{float(value):.1f}%"


def _eur(value: Any) -> str:
    return "-" if value is None else f"EUR {float(value):,.2f}"


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _truncate(text: str, width: int = 46) -> str:
    text = (text or "").replace("|", "/").strip()
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def market_section(markets: Sequence[Mapping[str, Any]]) -> str:
    rows = [[
        MARKET_LABEL.get(m["market"], m["market"]),
        _n(m["companies_found"]), _n(m["contacts"]), _n(m["touches_sent"]),
        _n(m["replies"]), _n(m["positive_replies"]), _pct(m["reply_rate_pct"]),
        _n(m["leads"]), _n(m["qualified_leads"]),
        _n(m["meetings_booked"]), _n(m["meetings_held"]),
        _n(m["pilots_started"]), _n(m["pilots_converted"]),
        _eur(m["mrr_eur"]),
    ] for m in markets]
    return _table(
        ["Market", "Firms", "Contacts", "Touches", "Replies", "Positive",
         "Reply rate", "Leads", "Qualified", "Booked", "Held", "Pilots",
         "Converted", "MRR"],
        rows)


def channel_section(channels: Sequence[Mapping[str, Any]]) -> str:
    rows = [[
        CHANNEL_LABEL.get(c["channel"], c["channel"]),
        _n(c["touches_sent"]) if c["touches_sent"] else "-",
        _n(c["leads"]), _n(c["qualified_leads"]), _n(c["too_small_leads"]),
        _n(c["meetings_booked"]), _n(c["meetings_held"]),
        _eur(c["spend_eur"]) if c["spend_eur"] else "-",
        _eur(c["cost_per_lead_eur"]),
        _eur(c["cost_per_booked_call_eur"]),
    ] for c in channels]
    return _table(
        ["Channel", "Touches", "Leads", "Qualified", "Too small", "Booked",
         "Held", "Spend", "Cost / lead", "Cost / booked call"],
        rows)


def content_section(content: Sequence[Mapping[str, Any]]) -> str:
    """Top three and bottom three, measured items only."""
    measured = [c for c in content if c.get("engagement_rate_pct") is not None]
    if not measured:
        return ("No content has a snapshot with views yet, so there is nothing "
                "to rank. Reels published this week appear here once the first "
                "stats snapshot lands.")

    ordered = sorted(measured, key=lambda c: c["engagement_rate_pct"],
                     reverse=True)

    def block(items: Sequence[Mapping[str, Any]]) -> str:
        return _table(
            ["Lane", "Lang", "Platform", "Views", "Engagement", "Click rate",
             "Hook"],
            [[LANE_LABEL.get(c["lane"], c["lane"]), c["language"],
              c["platform"], _n(c["views"]), _pct(c["engagement_rate_pct"]),
              _pct(c["click_rate_pct"]), _truncate(c["hook"])]
             for c in items])

    parts = ["**Top three**", "", block(ordered[:3])]
    if len(ordered) > 3:
        parts += ["", "**Bottom three**", "", block(ordered[-3:])]
    else:
        parts += ["", f"Only {len(ordered)} measured items, so there is no "
                      "separate bottom three."]

    unmeasured = len(content) - len(measured)
    if unmeasured:
        parts += ["", f"{unmeasured} published item(s) have a snapshot but no "
                      "views recorded yet. They are left out of both lists "
                      "rather than ranked last."]
    return "\n".join(parts)


def spend_section(channels: Sequence[Mapping[str, Any]]) -> str:
    ad = next((c for c in channels if c["channel"] == "ad"), None)
    if not ad or not ad["spend_eur"]:
        return ("No Meta spend recorded. The retargeting cap is EUR 500 a "
                "month; nothing has been drawn against it.")

    spend = float(ad["spend_eur"])
    lines = [
        f"- Spend so far: **{_eur(spend)}** of the EUR 500 monthly cap "
        f"({spend / 500 * 100:.0f}% used).",
        f"- Leads from ads: **{_n(ad['leads'])}**, of which "
        f"{_n(ad['qualified_leads'])} are 10+ seats and "
        f"{_n(ad['too_small_leads'])} are 1-9 seats.",
        f"- Cost per lead **{_eur(ad['cost_per_lead_eur'])}**, cost per booked "
        f"call **{_eur(ad['cost_per_booked_call_eur'])}**.",
    ]
    qualified = ad["qualified_leads"] or 0
    if qualified:
        lines.append(
            f"- Cost per *qualified* lead **{_eur(spend / qualified)}** - the "
            "number that matters, since 1-9 seat leads get redirected to the "
            "self-serve pricing page and never reach a call.")
    if ad["too_small_leads"] and ad["leads"]:
        share = ad["too_small_leads"] / ad["leads"] * 100
        lines.append(
            f"- {share:.0f}% of ad leads are too small for the offer. That is "
            "a targeting cost, not a conversion problem.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# What the numbers suggest
# ---------------------------------------------------------------------------

def suggestions(markets: Sequence[Mapping[str, Any]],
                channels: Sequence[Mapping[str, Any]],
                content: Sequence[Mapping[str, Any]]) -> str:
    out: list[str] = []

    # -- markets ------------------------------------------------------------
    active = [m for m in markets if m["touches_sent"] or m["leads"]]
    idle = [m for m in markets if not (m["touches_sent"] or m["leads"])]

    if len(active) >= 2:
        best = max(active, key=lambda m: (m["meetings_held"],
                                          m["meetings_booked"],
                                          m["qualified_leads"]))
        worst = min(active, key=lambda m: (m["meetings_held"],
                                           m["meetings_booked"],
                                           m["qualified_leads"]))
        if best["market"] != worst["market"]:
            out.append(
                f"- **{MARKET_LABEL[worst['market']]} is the weakest market so "
                f"far.** {_n(worst['touches_sent'])} touches produced "
                f"{_n(worst['replies'])} replies "
                f"({_n(worst['positive_replies'])} positive), "
                f"{_n(worst['leads'])} leads and "
                f"{_n(worst['meetings_held'])} held calls. "
                f"{MARKET_LABEL[best['market']]} on "
                f"{_n(best['touches_sent'])} touches produced "
                f"{_n(best['meetings_held'])} held calls and "
                f"{_eur(best['mrr_eur'])} MRR.")
            thin = [m for m in (best, worst) if m["touches_sent"] < THIN_EVIDENCE]
            if thin:
                out.append(
                    f"  Both sides of that comparison are under "
                    f"{THIN_EVIDENCE} touches, so it is a hint, not a result. "
                    "Do not cut a market on it yet.")

    for m in active:
        if m["leads"] and not m["meetings_booked"]:
            out.append(
                f"- **{MARKET_LABEL[m['market']]} generates leads but no "
                f"calls.** {_n(m['leads'])} leads, "
                f"{_n(m['qualified_leads'])} of them qualified, "
                "0 booked. That points at the booking step, not the top of the "
                "funnel.")
        if m["meetings_booked"] and not m["meetings_held"]:
            # Deliberately not phrased as "calls that do not happen": the view
            # counts every booked meeting, including ones still in the future.
            # Asserting a no-show problem from this column would be reading a
            # failure into a calendar that has not happened yet.
            out.append(
                f"- **{MARKET_LABEL[m['market']]} has "
                f"{_n(m['meetings_booked'])} booked call(s) and none held.** "
                "Check whether those dates have passed before treating it as a "
                "no-show problem.")

    for m in idle:
        out.append(
            f"- **{MARKET_LABEL[m['market']]} has no activity recorded.** Zero "
            "touches and zero leads. That is a missing input, not "
            "an underperforming one - nothing has been tried there yet.")

    # -- channels -----------------------------------------------------------
    paid = [c for c in channels if c["cost_per_booked_call_eur"] is not None]
    for c in paid:
        out.append(
            f"- **{CHANNEL_LABEL.get(c['channel'], c['channel'])} costs "
            f"{_eur(c['cost_per_booked_call_eur'])} per booked call** at "
            f"{_eur(c['spend_eur'])} spent. Against $89 per seat per month, "
            "one 10 seat firm covers that in the first week of a paid month.")

    for c in channels:
        if c["leads"] and not c["meetings_booked"]:
            out.append(
                f"- **{CHANNEL_LABEL.get(c['channel'], c['channel'])} brought "
                f"{_n(c['leads'])} leads and no booked calls.**")
        if c["too_small_leads"] and c["leads"]:
            share = c["too_small_leads"] / c["leads"]
            if share >= 0.3:
                out.append(
                    f"- **{CHANNEL_LABEL.get(c['channel'], c['channel'])} is "
                    f"pulling the wrong size of firm.** "
                    f"{_n(c['too_small_leads'])} of {_n(c['leads'])} leads are "
                    "1-9 seats.")

    # -- content lanes ------------------------------------------------------
    measured = [c for c in content if c.get("engagement_rate_pct") is not None]
    if measured:
        by_lane: dict[str, list[float]] = {}
        views_by_lane: dict[str, int] = {}
        for c in measured:
            by_lane.setdefault(c["lane"], []).append(
                float(c["engagement_rate_pct"]))
            views_by_lane[c["lane"]] = views_by_lane.get(c["lane"], 0) + int(
                c["views"] or 0)
        means = {lane: sum(v) / len(v) for lane, v in by_lane.items()}
        if len(means) >= 2:
            best_lane = max(means, key=means.get)
            worst_lane = min(means, key=means.get)
            out.append(
                f"- **{LANE_LABEL.get(best_lane, best_lane)} is the strongest "
                f"lane on engagement** at {means[best_lane]:.2f}% across "
                f"{len(by_lane[best_lane])} placement(s), against "
                f"{LANE_LABEL.get(worst_lane, worst_lane)} at "
                f"{means[worst_lane]:.2f}%.")
            if views_by_lane[best_lane] < 5000:
                out.append(
                    f"  That is on {views_by_lane[best_lane]:,} total views. "
                    "One reel landing well moves this number, so treat the lane "
                    "ranking as provisional until each lane has a few thousand "
                    "views per placement.")

        weak_lang = [c for c in measured
                     if c["language"] in ("da", "lt")
                     and float(c["engagement_rate_pct"]) < 2.0]
        if weak_lang:
            out.append(
                f"- {len(weak_lang)} localised item(s) are under 2% "
                "engagement. Worth checking the dub quality before concluding "
                "the market does not want the content - both languages ship "
                "marked NEEDS NATIVE CHECK.")

    if not out:
        out.append("- Nothing in the data separates the markets or the "
                   "channels yet. No suggestion would be honest this week.")
    return "\n".join(out)


def raw_numbers(markets: Sequence[Mapping[str, Any]],
                channels: Sequence[Mapping[str, Any]]) -> str:
    """The last thing on the page: the figures, with nothing read into them."""
    lines = ["Market: touches / replies / positive / leads / qualified / booked / "
             "held / pilots / converted / MRR"]
    for m in markets:
        lines.append(
            f"  {MARKET_LABEL.get(m['market'], m['market'])}: "
            f"{_n(m['touches_sent'])} / {_n(m['replies'])} / "
            f"{_n(m['positive_replies'])} / {_n(m['leads'])} / "
            f"{_n(m['qualified_leads'])} / {_n(m['meetings_booked'])} / "
            f"{_n(m['meetings_held'])} / {_n(m['pilots_started'])} / "
            f"{_n(m['pilots_converted'])} / {_eur(m['mrr_eur'])}")
    lines.append("")
    lines.append("Channel: leads / qualified / too small / booked / held / spend")
    for c in channels:
        lines.append(
            f"  {CHANNEL_LABEL.get(c['channel'], c['channel'])}: "
            f"{_n(c['leads'])} / {_n(c['qualified_leads'])} / "
            f"{_n(c['too_small_leads'])} / {_n(c['meetings_booked'])} / "
            f"{_n(c['meetings_held'])} / {_eur(c['spend_eur'])}")
    return "```\n" + "\n".join(lines) + "\n```"


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def render(as_of: dt.date | None = None) -> str:
    as_of = as_of or dt.date.today()
    markets = db.get_market_funnel()
    channels = db.get_channel_funnel()
    content = db.get_content_perf()

    totals = {
        "leads": sum(m["leads"] for m in markets),
        "booked": sum(m["meetings_booked"] for m in markets),
        "held": sum(m["meetings_held"] for m in markets),
        "converted": sum(m["pilots_converted"] for m in markets),
        "mrr": sum(float(m["mrr_eur"] or 0) for m in markets),
    }

    # A brief rendered from the SQLite mirror says so, in the first line. An
    # example brief committed to the repo would otherwise be indistinguishable
    # from a real one, and "Lithuania converted a pilot" is exactly the kind of
    # number nobody should read off demo data by mistake.
    banner = ("> **SEED DATA, NOT REAL.** Rendered from the local SQLite "
              "mirror and `seed/seed_demo.py`. Every figure below is invented.\n\n"
              if type(db._CLIENT).__name__ == "SqliteMirrorClient" else "")

    return f"""{banner}# DoviLoop Teams - Friday brief

**Week ending {as_of.isoformat()}** · campaign window 8 Sept - 19 Oct 2026
· $89 per seat per month + $500 setup · 10+ seats, no in-house dev team

{totals['leads']} leads, {totals['booked']} calls booked, {totals['held']} held,
{totals['converted']} {'pilot' if totals['converted'] == 1 else 'pilots'} converted, {_eur(totals['mrr'])} MRR.

---

## The three markets

{market_section(markets)}

Replies is every touch that got a classified reply, whatever the class. Positive
is `interested` or `referred` only: `not_now` and `objection` are replies but not
positive, `not_a_fit` and `unsubscribe` are replies and negative. Reply rate is
replies over touches. Qualified counts both `qualified` and `gmail_on_request` -
a Gmail firm of 10+ seats is a real lead, it just gets the booking link on
request.

---

## The channels

{channel_section(channels)}

Touches, and therefore reply rate, exist only for outreach: reels and ads have
no contact list upstream of the landing page. Compare those two from the leads
column rightwards.

---

## Content

{content_section(content)}

---

## Ad spend against leads

{spend_section(channels)}

---

## What the numbers suggest

{suggestions(markets, channels, content)}

---

## The numbers this rests on

{raw_numbers(markets, channels)}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the Friday brief.")
    parser.add_argument("--local", action="store_true",
                        help="use the in-memory SQLite mirror and seed data "
                             "instead of a real database")
    parser.add_argument("--date", help="week-ending date, YYYY-MM-DD "
                                       "(default: today)")
    parser.add_argument("--out-dir", default=str(ROOT / "briefs"))
    parser.add_argument("--no-write", action="store_true",
                        help="print only, do not write the file")
    args = parser.parse_args(argv)

    if args.local:
        sys.path.insert(0, str(ROOT / "tests"))
        sys.path.insert(0, str(ROOT / "seed"))
        import sqlite_mirror
        import seed_demo
        sqlite_mirror.install()
        seed_demo.seed(verbose=False)

    as_of = (dt.date.fromisoformat(args.date) if args.date
             else dt.date.today())
    text = render(as_of)

    if not args.no_write:
        out_dir = pathlib.Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{as_of.isoformat()}.md"
        path.write_text(text)
        print(f"written to {path}\n", file=sys.stderr)

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
