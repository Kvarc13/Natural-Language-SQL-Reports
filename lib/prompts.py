"""
lib/prompts.py — Load the business prompt docs from S3, with a TTL cache.

These are the schemas/prompts/*.md files (glossary, analysis rules, output
format, user lookup, core rules) assembled into the zeropark_analyst prompt.
Editing them on S3 changes behaviour without a redeploy: a warm container
reloads once the TTL lapses (lib/cache.py; TTL=0 keeps the first copy for the
whole container lifetime, so edits then need a forced cold start).

A missing doc degrades gracefully to "" — the server never fails to start
over a missing file, and the built-in fallback in tools/prompt.py still
applies.
"""
from lib import config
from lib.cache import TtlLoader
from lib.s3 import get_text

_PROMPT_KEYS = {
    "glossary": f"{config.SCHEMAS_PROMPTS_PREFIX}/glossary.md",
    "analysis_rules": f"{config.SCHEMAS_PROMPTS_PREFIX}/analysis_rules.md",
    "report_output_format": f"{config.SCHEMAS_PROMPTS_PREFIX}/report_output_format.md",
    "user_lookup": f"{config.SCHEMAS_PROMPTS_PREFIX}/user_lookup.md",
    "core_rules": f"{config.SCHEMAS_PROMPTS_PREFIX}/core_rules.md",
}


def _load() -> dict[str, str]:
    return {name: get_text(key) for name, key in _PROMPT_KEYS.items()}


_loader = TtlLoader(_load)


def get_prompt_docs() -> dict[str, str]:
    """Return {name: markdown}. Reloads from S3 when the TTL has lapsed."""
    return _loader.get()
