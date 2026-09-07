## IDENTIFYING PLATFORM USERS (Publisher / Advertiser)

When the user mentions a company, person, or service (e.g. "Sovrn", "Parking Crew", "Opera"),
the simplest path is the lookup_feed_or_advertiser tool — it runs the fuzzy matching described
below and returns publisher_feed_hash plus suggested_report (the report type matched to the
feeds' revenue_stream). One call to that tool is usually enough.

The SQL queries below document what lookup_feed_or_advertiser does internally — you can run
them directly via query_data if you need a non-standard search.

Two files on S3 hold the user and feed database:
- s3://zeropark-reports-datalake/schemas/lookup/PublisherFeeds.csv — publishers + their feeds
  (columns: user_id, user_first_name, user_last_name, user_email, user_type, user_company_name,
  publisher_feed_name, publisher_feed_hash, publisher_feed_id, publisher_revenue_stream, is_injection, is_magic)
- s3://zeropark-reports-datalake/schemas/lookup/Users.csv — all platform users (~13k,
  columns: user_id, user_first_name, user_last_name, user_email, user_type, user_company_name)

### STEP 1 — Search publishers + feeds (fuzzy matching)
```sql
WITH search AS (SELECT '{search_phrase}' AS q)
SELECT DISTINCT user_first_name, user_last_name, user_email,
       publisher_feed_name, publisher_feed_hash, publisher_feed_id, publisher_revenue_stream
FROM read_csv_auto('s3://zeropark-reports-datalake/schemas/lookup/PublisherFeeds.csv', all_varchar=true), search
WHERE user_email ILIKE '%' || q || '%'
   OR publisher_feed_name ILIKE '%' || q || '%'
   OR jaro_winkler_similarity(LOWER(user_last_name), LOWER(q)) > 0.85
   OR jaro_winkler_similarity(LOWER(SPLIT_PART(user_email, '@', 2)), LOWER(q) || '.com') > 0.85
   OR jaro_winkler_similarity(LOWER(REGEXP_REPLACE(publisher_feed_name, ' \|.*', '')), LOWER(q)) > 0.8
ORDER BY publisher_feed_name
```
ILIKE catches exact substrings. jaro_winkler catches typos (e.g. "Sovn" → "Sovrn", threshold >0.8).
REGEXP_REPLACE strips everything after the first " |" to compare the base publisher name in the feed.

### STEP 2 — If no publisher matches, search advertisers
```sql
WITH search AS (SELECT '{search_phrase}' AS q)
SELECT user_id, user_first_name, user_last_name, user_email, user_type, user_company_name
FROM read_csv_auto('s3://zeropark-reports-datalake/schemas/lookup/Users.csv', all_varchar=true), search
WHERE user_email ILIKE '%' || q || '%'
   OR user_company_name ILIKE '%' || q || '%'
   OR jaro_winkler_similarity(LOWER(user_last_name), LOWER(q)) > 0.85
   OR jaro_winkler_similarity(LOWER(user_first_name || ' ' || user_last_name), LOWER(q)) > 0.8
LIMIT 20
```
If user_type = Advertiser → tell the user this is an Advertiser (no publisher feeds).

### STEP 3 — Filter by traffic type (optional)
The publisher_revenue_stream column is a feed's traffic type: DOMAIN, SEARCH, POPUP, MAGIC, SHOPNOMIX.
It is NOT a user identity — it's a property of the feed.
If the user asks for a specific type (e.g. "Search feeds from Sovrn"): add to WHERE
`AND publisher_revenue_stream = 'SEARCH'`. If they don't specify — show all feeds.

### STEP 4 — Confirm with the user
Show the matched feeds as a table (columns: publisher_feed_name, publisher_feed_hash,
publisher_revenue_stream) and ask which feeds they want to use.
Do NOT show user_email, user_id, or other personal data — it's for matching only.
Group results by publisher_revenue_stream if a publisher has feeds of different types.

### STEP 5 — After confirmation
Use publisher_feed_hash as the PUBLISHER_FEED filter value and proceed with the standard flow:
get_report_schema → generate_zeropark_report. (In the Claude app there is no separate
parameter-confirmation step — just state in chat which parameters you'll use, then call generate.)

Filter example: {"name": "PUBLISHER_FEED", "values": ["ibis-leopard", "columbine-fowl"]}

Pick the report type based on the feeds' revenue_stream (lookup_feed_or_advertiser returns this as suggested_report):
SEARCH/SHOPNOMIX → SearchMasterReportRow, DOMAIN/POPUP → MasterReportRow.

IMPORTANT:
- The PUBLISHER_FEED filter takes publisher_feed_hash (e.g. "ibis-leopard"), NOT the feed name.
- One publisher can have many feeds — show them all, let the user choose.
- If a search returns >10 results, ask the user to narrow it down.
