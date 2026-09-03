-- =====================================================================
-- campaign-ledger / 003_views.sql
-- The three views the Friday brief reads.
--
-- TARGET PROJECT: oqpeebtwtikdzorgouxd   (confirm before running - see B-1)
-- Run order: 001_schema.sql -> 002_rls.sql -> 003_views.sql
-- Re-runnable: yes (CREATE OR REPLACE).
--
-- Two deliberate choices worth knowing before you read the SQL:
--
-- 1. Every metric is its own aggregate CTE, joined at the end on the grouping
--    key. The obvious alternative - one long chain of LEFT JOINs from companies
--    down to pilots - multiplies rows at every one-to-many step and silently
--    inflates the counts above it. A firm with 4 contacts would count as 4
--    companies. These numbers decide which market gets killed, so they are
--    computed independently and joined once.
--
-- 2. Enum columns are cast to text. It keeps the views readable from any
--    client without enum type mapping, and keeps the grouping key stable if an
--    enum ever gains a label.
-- =====================================================================

-- ---------------------------------------------------------------------
-- v_market_funnel
--
-- One row per market, always three rows, zeros included. This is the view the
-- three-market A/B test gets decided on, so a market with no activity has to
-- show up as a line of zeros rather than vanish from the table.
--
-- booked_calls_per_100_contacts is the headline number from the spec.
-- ---------------------------------------------------------------------

create or replace view campaign.v_market_funnel as
with markets (market, sort_order) as (
  select 'dk', 1
  union all select 'lt', 2
  union all select 'global', 3
),
co as (
  select cast(market as text) as market, count(*) as companies_found
  from campaign.companies
  group by cast(market as text)
),
ct as (
  select cast(market as text) as market, count(*) as contacts
  from campaign.contacts
  group by cast(market as text)
),
tc as (
  select
    cast(c.market as text) as market,
    count(*) as touches_sent,
    count(t.replied_at) as replies,
    count(*) filter (
      where cast(t.reply_sentiment as text) in ('hot_pain', 'curious', 'endorse')
    ) as positive_replies
  from campaign.touches t
  join campaign.contacts c on c.id = t.contact_id
  group by cast(c.market as text)
),
ld as (
  select
    cast(market as text) as market,
    count(*) as leads,
    count(*) filter (
      where cast(stage as text) in ('qualified', 'gmail_on_request')
    ) as qualified_leads,
    count(*) filter (where cast(stage as text) = 'too_small') as too_small_leads
  from campaign.leads
  group by cast(market as text)
),
mt as (
  select
    cast(l.market as text) as market,
    count(*) as meetings_booked,
    count(*) filter (where cast(m.status as text) = 'held') as meetings_held
  from campaign.meetings m
  join campaign.leads l on l.id = m.lead_id
  group by cast(l.market as text)
),
pl as (
  select
    cast(l.market as text) as market,
    count(*) as pilots_started,
    count(*) filter (where p.converted) as pilots_converted,
    coalesce(sum(case when p.converted then p.mrr_eur else 0 end), 0) as mrr_eur
  from campaign.pilots p
  join campaign.leads l on l.id = p.lead_id
  group by cast(l.market as text)
)
select
  m.market,
  coalesce(co.companies_found, 0)  as companies_found,
  coalesce(ct.contacts, 0)         as contacts,
  coalesce(tc.touches_sent, 0)     as touches_sent,
  coalesce(tc.replies, 0)          as replies,
  coalesce(tc.positive_replies, 0) as positive_replies,
  round(100.0 * coalesce(tc.replies, 0) / nullif(tc.touches_sent, 0), 1) as reply_rate_pct,
  coalesce(ld.leads, 0)            as leads,
  coalesce(ld.qualified_leads, 0)  as qualified_leads,
  coalesce(ld.too_small_leads, 0)  as too_small_leads,
  coalesce(mt.meetings_booked, 0)  as meetings_booked,
  coalesce(mt.meetings_held, 0)    as meetings_held,
  coalesce(pl.pilots_started, 0)   as pilots_started,
  coalesce(pl.pilots_converted, 0) as pilots_converted,
  coalesce(pl.mrr_eur, 0)          as mrr_eur,
  -- Read this one only for markets where outreach is the main channel. A
  -- market whose calls came from ads or reels has meetings in the numerator
  -- that no contact in the denominator produced, which inflates it.
  round(100.0 * coalesce(mt.meetings_booked, 0) / nullif(ct.contacts, 0), 1)
    as booked_calls_per_100_contacts,
  m.sort_order
from markets m
left join co on co.market = m.market
left join ct on ct.market = m.market
left join tc on tc.market = m.market
left join ld on ld.market = m.market
left join mt on mt.market = m.market
left join pl on pl.market = m.market
order by m.sort_order;

comment on view campaign.v_market_funnel is
  'One row per market, zeros included. Decides which of dk / lt / global to keep.';

-- ---------------------------------------------------------------------
-- v_channel_funnel
--
-- Same shape, grouped by acquisition source instead of market, so reels, ads
-- and outreach can be compared on cost per booked call.
--
-- One honest asymmetry, stated here rather than hidden in the numbers:
-- companies, contacts, touches and replies only exist for OUTREACH. Reels and
-- ads have no contact list upstream of the landing page, so those cells are 0
-- for them by definition, not because nothing happened. Read the leads column
-- onwards when comparing reel against ad.
--
-- Likewise only ads carry spend, so cost_per_booked_call_eur is NULL for every
-- other channel. That is not a gap in the data - the other channels cost time,
-- not euros, and putting a fabricated number there would make reels look
-- expensive or free depending on the fabrication.
-- ---------------------------------------------------------------------

create or replace view campaign.v_channel_funnel as
with channels (channel, sort_order) as (
  select 'outreach', 1
  union all select 'reel', 2
  union all select 'ad', 3
  union all select 'direct', 4
),
co as (
  select 'outreach' as channel, count(*) as companies_found
  from campaign.companies
),
ct as (
  select 'outreach' as channel, count(*) as contacts
  from campaign.contacts
),
tc as (
  select
    'outreach' as channel,
    count(*) as touches_sent,
    count(t.replied_at) as replies,
    count(*) filter (
      where cast(t.reply_sentiment as text) in ('hot_pain', 'curious', 'endorse')
    ) as positive_replies
  from campaign.touches t
),
ld as (
  select
    cast(source as text) as channel,
    count(*) as leads,
    count(*) filter (
      where cast(stage as text) in ('qualified', 'gmail_on_request')
    ) as qualified_leads,
    count(*) filter (where cast(stage as text) = 'too_small') as too_small_leads
  from campaign.leads
  group by cast(source as text)
),
mt as (
  select
    cast(l.source as text) as channel,
    count(*) as meetings_booked,
    count(*) filter (where cast(m.status as text) = 'held') as meetings_held
  from campaign.meetings m
  join campaign.leads l on l.id = m.lead_id
  group by cast(l.source as text)
),
pl as (
  select
    cast(l.source as text) as channel,
    count(*) as pilots_started,
    count(*) filter (where p.converted) as pilots_converted,
    coalesce(sum(case when p.converted then p.mrr_eur else 0 end), 0) as mrr_eur
  from campaign.pilots p
  join campaign.leads l on l.id = p.lead_id
  group by cast(l.source as text)
),
sp as (
  select
    'ad' as channel,
    coalesce(sum(spend_eur), 0) as spend_eur,
    coalesce(sum(impressions), 0) as impressions,
    coalesce(sum(clicks), 0) as ad_clicks
  from campaign.ad_stats
)
select
  ch.channel,
  coalesce(co.companies_found, 0)  as companies_found,
  coalesce(ct.contacts, 0)         as contacts,
  coalesce(tc.touches_sent, 0)     as touches_sent,
  coalesce(tc.replies, 0)          as replies,
  coalesce(tc.positive_replies, 0) as positive_replies,
  round(100.0 * coalesce(tc.replies, 0) / nullif(tc.touches_sent, 0), 1) as reply_rate_pct,
  coalesce(ld.leads, 0)            as leads,
  coalesce(ld.qualified_leads, 0)  as qualified_leads,
  coalesce(ld.too_small_leads, 0)  as too_small_leads,
  coalesce(mt.meetings_booked, 0)  as meetings_booked,
  coalesce(mt.meetings_held, 0)    as meetings_held,
  coalesce(pl.pilots_started, 0)   as pilots_started,
  coalesce(pl.pilots_converted, 0) as pilots_converted,
  coalesce(pl.mrr_eur, 0)          as mrr_eur,
  coalesce(sp.spend_eur, 0)        as spend_eur,
  coalesce(sp.impressions, 0)      as impressions,
  -- No coalesce on spend here, deliberately. A channel with no spend gets
  -- NULL, not 0.00: "outreach costs EUR 0.00 per booked call" is a lie that
  -- would make ads look infinitely worse than a channel that costs Dovy's
  -- whole week.
  round(sp.spend_eur / nullif(mt.meetings_booked, 0), 2)
    as cost_per_booked_call_eur,
  round(sp.spend_eur / nullif(ld.leads, 0), 2)
    as cost_per_lead_eur,
  ch.sort_order
from channels ch
left join co on co.channel = ch.channel
left join ct on ct.channel = ch.channel
left join tc on tc.channel = ch.channel
left join ld on ld.channel = ch.channel
left join mt on mt.channel = ch.channel
left join pl on pl.channel = ch.channel
left join sp on sp.channel = ch.channel
order by ch.sort_order;

comment on view campaign.v_channel_funnel is
  'One row per acquisition source. companies/contacts/touches are outreach-only by construction; spend is ad-only.';

-- ---------------------------------------------------------------------
-- v_content_perf
--
-- One row per content item per platform, using only that pair's most recent
-- snapshot. This is what tells you whether Higgsfield, HyperFrames or Dovy on
-- camera actually wins.
--
-- engagement_rate_pct is (likes + comments + saves) / views. It is NULL, not 0,
-- when a piece has no views yet - a brand new reel is unmeasured, not bad.
-- Ordered so that NULLs sort last, which keeps the unmeasured ones out of the
-- "bottom three" in the Friday brief.
-- ---------------------------------------------------------------------

create or replace view campaign.v_content_perf as
with latest as (
  select cs.*
  from campaign.content_stats cs
  join (
    select content_id, platform, max(captured_on) as captured_on
    from campaign.content_stats
    group by content_id, platform
  ) mx
    on mx.content_id = cs.content_id
   and mx.platform = cs.platform
   and mx.captured_on = cs.captured_on
)
select
  c.id                        as content_id,
  cast(c.kind as text)        as kind,
  cast(c.lane as text)        as lane,
  cast(c.language as text)    as language,
  c.hook,
  l.platform,
  l.captured_on               as stats_captured_on,
  l.views,
  l.likes,
  l.comments,
  l.saves,
  l.clicks,
  (l.likes + l.comments + l.saves) as engagements,
  round(100.0 * (l.likes + l.comments + l.saves) / nullif(l.views, 0), 2)
    as engagement_rate_pct,
  round(100.0 * l.clicks / nullif(l.views, 0), 2) as click_rate_pct,
  c.published_at,
  (current_date - cast(c.published_at as date)) as days_since_publish
from campaign.content c
join latest l on l.content_id = c.id
order by engagement_rate_pct desc nulls last, l.views desc;

comment on view campaign.v_content_perf is
  'Latest snapshot per content item per platform, ordered by engagement rate. Unmeasured items sort last.';

-- ---------------------------------------------------------------------
-- Views inherit the schema-level revokes from 002_rls.sql, but state it again
-- so a view can never become the hole in the fence.
--
-- On Postgres 15+ you can additionally pin each view to the caller's
-- privileges with:
--   alter view campaign.v_market_funnel set (security_invoker = true);
-- Left out of the migration so this file runs on older instances too.
-- ---------------------------------------------------------------------

revoke all on campaign.v_market_funnel  from anon, authenticated;
revoke all on campaign.v_channel_funnel from anon, authenticated;
revoke all on campaign.v_content_perf   from anon, authenticated;

grant select on campaign.v_market_funnel  to service_role;
grant select on campaign.v_channel_funnel to service_role;
grant select on campaign.v_content_perf   to service_role;
