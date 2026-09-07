"""tools/prompt.py — the zeropark_analyst MCP prompt + about tool.

Thin logic only: inject today's date, load the core rules and business docs from
S3 (schemas/prompts/*.md), assemble. ALL prompt TEXT now lives on S3 and is
editable without a redeploy — core_rules.md carries the app-specific hard rules
(schema-first, routing, anti-hallucination) that used to be hard-coded here.
"""
from datetime import datetime, timezone

from lib import config
from lib.prompts import get_prompt_docs
from lib.s3 import get_text


def register(mcp):
    @mcp.tool()
    def about_zeropark_reports() -> str:
        """CALL THIS whenever the user asks what this connector/integration can
        do, what's possible with it, how it works, what reports exist, or asks
        for help/instructions — including generic phrasings like 'co
        potrafisz?', 'what can you do?', 'help', 'jak to działa?'. Returns the
        authoritative capabilities document (features, example requests,
        available reports, key rules, limits). Prefer this over improvising
        from tool names. Answer in the user's language, summarized naturally —
        don't dump the raw document unless asked."""
        return get_text(f"{config.SCHEMAS_PROMPTS_PREFIX}/readme.md") or (
            "Zeropark Reports MCP: generate Zeropark reports and analyze them "
            "with SQL on your private S3 workspace. Start with "
            "get_report_schema('_index')."
        )

    @mcp.prompt()
    def zeropark_analyst() -> str:
        """System guidance for working with Zeropark reports over MCP."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        docs = get_prompt_docs()

        # Core rules live on S3 (core_rules.md); date is injected at runtime.
        # Built-in fallback keeps the server safe if the S3 doc is missing.
        core = docs.get("core_rules", "") or (
            "You are a Zeropark reporting analyst. ALWAYS call "
            "get_report_schema('_index') FIRST and pick a report from the list "
            "before generating. query_data is the ONLY valid source of numbers."
        )
        core = f"Today (UTC) is {today}.\n\n" + core

        sections = [core]
        for name in ("glossary", "analysis_rules", "report_output_format", "user_lookup"):
            doc = docs.get(name, "")
            if doc:
                sections.append(doc)
        return "\n\n".join(sections)
