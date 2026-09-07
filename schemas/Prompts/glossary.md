# GLOSSARY & DOMAIN KNOWLEDGE — ZEROPARK

## THE PLATFORM
Zeropark is an ad marketplace connecting **Publishers** (traffic supply) with
**Advertisers** (demand). A publisher sends traffic (a redirect after an ad
click) through Zeropark's ad server; Zeropark matches it to advertiser
campaigns, computes margin and publisher payout, and redirects to the
campaign's target URL. Zeropark has no creatives: on POP and Domain the ad
opens a full browser; on Commerce Media the publisher displays brand
icons/products/links and sends the clicks to Zeropark.

## ENTITIES — SUPPLY SIDE
- **Publisher** (aka Supply) — account sending traffic. Two kinds: Ad Exchange
  and Direct Publisher.
- **Source = Publisher Feed** (aka ad tag, endpoint) — one traffic stream from
  a publisher, shown to advertisers as a random color-animal pair (e.g.
  `porraceous-wild`). Has its own margin, filtering, allowed campaign
  categories, integration type. 1 Publisher → many Sources; 1 Source → many
  Placements. `publisher_feed_hash` is the value the PUBLISHER_FEED filter takes.
- **Placement = Target** (aka SubId) — most granular traffic unit: a site, app
  or brand within a Source (auto-created from `domain` + `domain_id`).
  1 Placement → exactly 1 Source. Exchanges generate millions of placements.
- **External Publisher** (aka sub-publisher) — sub-publisher ID sent by ad
  exchanges within one Source. Commerce Media (Search) reports only.
- **Placement Category** (aka Inventory Category) — thematic grouping of
  placements (Coupons, BNPL, Cashback, Browser Extensions...).
- `publisher_revenue_stream` — a feed's traffic type: DOMAIN, SEARCH, POPUP,
  MAGIC, SHOPNOMIX.

## ENTITIES — DEMAND SIDE
- **Advertiser** (aka Demand) — self-serve account running campaigns, buying traffic.
- **Advertiser Feed** (aka external demand, third-party demand partner) —
  external ad exchange integrated S2S; Zeropark sends bid requests and waits
  within a timeout. Separate UI and registration.
- **Campaign** — advertiser's buying unit with targeting, bid, budget.
  1 campaign = 1 ad format.
- **Campaign Category** — grouping by vertical (E-commerce, Nutra, Lead
  Generation, Adult...).
- **Brand** (aka brand domain, keyword in Search) — brand domain in Commerce
  Media traffic (e.g. `nike.com`). BRAND column — Search reports only.
- **Account Manager (AM)** — internal Zeropark employee managing
  publisher/advertiser relations; creates Sources, sets margins, filters,
  monitors traffic quality.
- **Bidding Algorithm** — bid-computation variant per Source/campaign.
  Commerce Media (Search) reports only.

## AD FORMATS
- **Commerce Media** (aka Search, Branded Ads, Homepage) — brand ads: Homepage,
  BNPL, Coupon, Browser Tiles, MSN/Outlook, Yahoo Mail, Commerce Content.
  Trusted advertisers only. **In report code and the API this is ALWAYS
  called "Search".**
- **Subpage (Deeplink)** — specific-product ads; trusted advertisers only.
- **POP** — pop-up/pop-under (streaming, extension injection, blogs). All advertisers.
- **Domain** — parked domains, Native arbitrage. All advertisers.
- **Push is discontinued** — historical data exists, no new data since 2024.

## INTEGRATIONS
- **RTB (Zeroclick)** — publisher sends the bid request to many demand partners
  at once; Zeropark answers with a bid or 0-bid; the visitor isn't lost on a
  loss. Post-visit validation (Invalidation) applies. 
- **Direct (Domestic)** — visitor redirected straight to Zeropark, no external
  auction; on no-match: fallback URL or visitor lost. Direct publishers depend
  heavily on coverage.

## TRAFFIC PIPELINE (order matters)
```
RAW_VISITS → TOTAL_VISITS → MATCHED_TARGETING → WINS → SOLD_VISITS → (INVALID_VISIT)
             (filtering)     (demand matching;   (internal (delivered   (post-click
                              BIDDINGS counted    auction   & billed)    validation,
                              per campaign)       winners)               RTB only)
```
Metric names and aliases (users may use ANY of these, incl. new CMT Platform names):
| Report column | Aliases / CMT Platform name |
|---|---|
| RAW_VISITS | Raw Visits, **Bid Requests** |
| TOTAL_VISITS | Total Visits, **Accepted Requests** |
| (RAW−TOTAL) | Filtered Requests, **Rejected Requests**; FILTERED_OUT% = (RAW−TOTAL)/RAW |
| MATCHED_TARGETING | Matched (demand match, before bid floors) |
| BIDDINGS | Bids, **Auctions** (counted per campaign) |
| WINS | **Bid Responses** (internal auction winners) |
| SOLD_VISITS | Sold Clicks, Sold, Redirects, Bought |
| INVALID_VISIT | Invalid Visits, **Invalidated Clicks** (RTB only) |

Key ratios: SOLD/TOTAL = supply monetization; WINS/TOTAL = internal demand
coverage; SOLD/WINS = externally-won share (RTB; high = competitive bids).
WINS > SOLD is normal (e.g. Sovrn may not confirm an external win).

## FINANCIAL METRICS
| Column | Aliases / CMT name | Meaning |
|---|---|---|
| ADVERTISER_COST | Spent, **Ad Spend**, Zeropark revenue | money spent by advertiser = Zeropark's revenue |
| PUBLISHER_PAYOUT | Publisher Revenue, traffic cost | paid to the publisher = traffic cost |
| PROFIT | **Gross Profit** | ADVERTISER_COST − PUBLISHER_PAYOUT |
| MARGIN | — | PROFIT / ADVERTISER_COST (NOT profit/payout) |
| ADVERTISER_PAYOUT | **Commission**, conversions revenue | advertiser's revenue from conversions (tracked by the advertiser). **≠ ADVERTISER_COST!** |
| RETURN_OF_INVESTMENT | ROI | (ADVERTISER_PAYOUT − ADVERTISER_COST) / ADVERTISER_COST |
Also: Avg. Cost (advertiser's avg price per visit), Avg. Payout (avg publisher
payout per visit), eCPA (avg conversion cost), Win Ratio% = Sold/Biddings
(campaign level).

## INVALIDATION (RTB only)
Invalidated clicks are EXCLUDED from Sold: no publisher payout, no advertiser
cost. Breakdown columns (UA/country/ISP/device/OS/browser/timezone mismatch,
duplicates, data-center IP, VM, webdriver, blocked UA...) exist in Search
master reports; INVALID_VISIT_PERCENT summarizes quality.

## SOVRN DISCREPANCIES (Search reports)
EXTERNAL_WINS = win confirmations from Sovrn; SOLD_DISCREPANCIES =
EXTERNAL_WINS − SOLD_VISITS; SOLD_DISCREPANCIES_PERCENT = share of won
auctions not counted as sold.

## REPORT TYPES — SCOPE (critical, do not double count)
- **MasterReportRow** ("Redirect") — **ALL Zeropark traffic: POP + Domain +
  Commerce Media/Search**. SEARCH_TILES rows are INCLUDED here. No Target
  dimension (millions of targets). Whole-Zeropark totals = THIS report alone.
- **SearchMasterReportRow** ("Search") — Commerce Media ONLY; adds BRAND,
  EXTERNAL_PUBLISHER, SEARCH_TYPE, PLACEMENT_*, BIDDING_ALGORITHM. Subset of
  the same traffic that's already in MasterReportRow.
- **SearchTargetMasterReportRow** — as above WITH Target (deepest analysis).
- **NEVER sum MasterReportRow with a Search\* report — Search gets counted
  twice.** Report-name conventions: "(Redirect)" = all traffic incl. Search;
  "(Search)" = Commerce Media only; "(All)" = all traffic.
- Filters marked `*` in a schema are REQUIRED (single value); others optional
  (multiple values).

## ANALYSIS PITFALLS
- DuckDB: always `all_varchar=true` in `read_csv_auto()` (avoids "Cannot mix
  VARCHAR and BIGINT"); column names case-insensitive.
- MARGIN must be aggregated weighted (sum PROFIT / sum ADVERTISER_COST), never
  averaged across rows.
- ADVERTISER_PAYOUT ≠ ADVERTISER_COST (commission vs spend) — picking the
  wrong one silently corrupts ROI/profit analyses.
- "Search" in report/API code = Commerce Media (business name).

## WHO ASKS WHAT (tailor the analysis)
- **AM** — effect of setting changes (margin, filtering); coverage, payout,
  FILTERED_OUT%, WINS/TOTAL.
- **Publisher-side** — coverage (WINS_TO_TOTAL), Publisher Payout, filtration
  rate, invalidation breakdown, Source comparison.
- **Advertiser-side** — Ad Spend, conversions, ROI, Win Ratio, performance per
  Target/Placement/Source.
- **Finance** — Profit, Margin, revenue streams, per-AM performance.
- **Compliance** — invalidation breakdown, traffic-quality ratios, filtration.
