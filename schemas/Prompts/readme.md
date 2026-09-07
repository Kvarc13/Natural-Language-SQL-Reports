# Zeropark Reports — what this integration can do

You are connected to the Zeropark reporting system. You can ask for reports and
analyses in plain language (Polish or English) — reports are generated from the
real Zeropark reports-api, and **every number is computed by SQL on AWS**, on
data stored in your private workspace. Nothing is estimated from memory.

## What you can ask for

- **Reports on demand:** "daily profit per feed for the last 7 days",
  "top 10 advertisers by profit across all of Zeropark", "traffic for
  ibis-leopard split per day", "compare Search vs Domain for this advertiser".
- **Filtering at the source:** by publisher feed, advertiser, country, brand
  and any filter the report supports — names are resolved automatically
  ("only Shopnomix feeds" works without knowing any hashes).
- **Follow-up analysis without regenerating:** once a report exists, further
  questions (rankings, ratios, day-by-day, "and now only X") run as SQL on the
  same data — instant and free.
- **Exports:** any full result can be written to CSV with a download link.
- **Lookups:** "who is <company/person>? what feeds do they run?" — resolves
  to publisher feeds, hashes, revenue streams.
- **Time aggregation:** hourly (`BY_HOUR` — only when you really need it),
  daily (`BY_DAY`), monthly (`BY_MONTH`), or totals for the whole range.

## Available reports

| Report | Covers |
|---|---|
| `MasterReportRow` | ALL Zeropark traffic: POP + Domain + Commerce Media/Search — use for whole-Zeropark questions |
| `SearchMasterReportRow` | Commerce Media/Search only — adds BRAND, EXTERNAL_PUBLISHER, PLACEMENT details |
| `SearchTargetMasterReportRow` | Search with per-Target granularity (deepest view) |

The Search reports are subsets of traffic already included in MasterReportRow —
they are never summed together (that would double-count Search).

## How your data is handled

- **Private workspace:** your reports live under your own S3 prefix — other
  users cannot see or query them. Reports are kept for ~48 h, then cleaned
  automatically (regenerating is cheap).
- **Personal access:** you signed in with your own account; access can be
  managed per person. Every request is logged (who, which tool, what
  parameters, result) for audit purposes.

## Good to know (limits & behaviors)

- Dates are hour-precision UTC; a "full day" ends at the NEXT day's 00 —
  handled automatically, mentioned in case you specify exact timestamps.
- **No time range given = the last 7 full days** by default (the assumption is
  stated in the answer).
- **Range limits per granularity** (protecting the reports API): hourly up to
  14 days, daily up to 185 days, monthly or whole-range totals up to 735 days.
  Bigger asks are declined with alternatives. Ranges over ~3 months of daily
  data or whole-range totals (or ~3 days hourly, ~6 months monthly) trigger
  a quick confirmation question before anything is generated.
- **Reports are generated one at a time** to keep the reporting API healthy;
  very large reports may take a few minutes — the system polls until they're
  ready.
- Inline result tables show up to 50 rows — larger results go to a CSV export
  with a download link.
- Historical Push-format data exists but the format was discontinued in 2024.
- Numbers policy: every figure quoted comes from an executed SQL query on the
  generated report. If something can't be computed from the data, you'll be
  told — not given an estimate.

## Example prompts to try

- "Raport dzienny per feed za ostatnie 7 dni, tylko ibis-leopard."
- "Top 10 advertisers by profit, whole Zeropark, last week."
- "Which feeds had the worst sold/total ratio yesterday?"
- "Compare margin per traffic type this month vs last month."
- "Export the full advertiser breakdown to CSV."
