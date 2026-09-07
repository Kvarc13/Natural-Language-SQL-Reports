"""tools/schema.py — get_report_schema."""
import json

from lib.s3 import get_schema_json


def register(mcp):
    @mcp.tool()
    def get_report_schema(report_name: str = "_index") -> str:
        """Get a Zeropark report schema: dimensions (grouped), metrics (grouped), filters.

        Call with report_name='_index' FIRST for the list of available reports and
        their traffic_type. The index lists ALL available reports — more than the
        three Master ones, so read it rather than assuming. The Master family
        differs: MasterReportRow
        (labelled "Redirect" but covering ALL traffic incl. Search/Commerce Media)
        has NO BRAND/CAMPAIGN_BRAND_URL dimension and no Search columns;
        SearchMasterReportRow and SearchTargetMasterReportRow (Search) do. Never
        assume MasterReportRow — if a requested dimension (e.g. BRAND) isn't in the
        schema you're looking at, check '_index' and the other reports before
        concluding it doesn't exist. Column and filter names must be EXACT matches
        from the chosen schema or the report fails.

        The schema groups dimensions into identity groups (e.g. advertiser_identity =
        ADVERTISER, ADVERTISER_ID, ADVERTISER_NAME, ADVERTISER_EMAIL). When the user
        says 'per advertiser' / 'per source' / 'per campaign', include the WHOLE
        matching identity group unless they explicitly ask for one column — IDs and
        hashes are useless without their NAME columns. Metrics are NOT expanded this
        way: pick metrics strictly as asked. Filter names in filters.required are
        mandatory. PUBLISHER_FEED filters take the publisher_feed_hash (e.g.
        'porraceous-wild'), not the feed display name.
        """
        text, found = get_schema_json(report_name)
        if not found:
            return json.dumps({
                "error": f"Schema '{report_name}' not found. Use '_index' to list reports."
            })
        return text
