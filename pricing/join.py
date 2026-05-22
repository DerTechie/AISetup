"""Join OpenRouter prices with Artificial Analysis scores via the id-map."""
import difflib
import re
from decimal import Decimal

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(text):
    """Lowercase and strip vendor prefix + non-alphanumerics for matching."""
    text = text.split(":", 1)[-1]  # drop "Vendor: " prefix if present
    return _NORM_RE.sub("", text.lower())


_INTELLIGENCE = "artificial_analysis_intelligence_index"
# Benchmark -> output column. tau2 (tool-use reliability) and terminalbench_hard
# (agentic task completion) are the axes the Intelligence Index misses; both are
# surfaced here so a route can be picked on tool-use, not just raw smarts.
_QUALITY_COLUMNS = {
    _INTELLIGENCE: "aa_intelligence_index",
    "tau2": "aa_tau2",
    "terminalbench_hard": "aa_terminalbench_hard",
}

JOIN_FIELDNAMES = [
    "openrouter_id", "name",
    "prompt_usd_per_mtok", "completion_usd_per_mtok", "blended_usd_per_mtok",
    "aa_intelligence_index", "aa_tau2", "aa_terminalbench_hard",
]


def _blend(prompt, completion):
    """3:1 input:output blended price; '' if either side is blank."""
    if not prompt or not completion:
        return ""
    value = (Decimal(prompt) * 3 + Decimal(completion)) / 4
    # `:f` renders plain decimal (no scientific notation); normalize drops trailing zeros.
    return f"{value.normalize():f}"


def build_price_vs_quality(price_rows, score_rows, map_rows):
    """One row per OpenRouter model: blended price + AA quality columns."""
    o2a = {m["openrouter_id"]: m["aa_slug"] for m in map_rows if m.get("aa_slug")}
    # benchmark -> {aa_slug: score}, only for the benchmarks we surface.
    scores = {benchmark: {} for benchmark in _QUALITY_COLUMNS}
    for s in score_rows:
        if s["benchmark"] in scores:
            scores[s["benchmark"]][s["source_model_name"]] = s["score"]
    rows = []
    for price in price_rows:
        slug = o2a.get(price["id"], "")
        prompt = price["prompt_usd_per_mtok"]
        completion = price["completion_usd_per_mtok"]
        row = {
            "openrouter_id": price["id"],
            "name": price["name"],
            "prompt_usd_per_mtok": prompt,
            "completion_usd_per_mtok": completion,
            "blended_usd_per_mtok": _blend(prompt, completion),
        }
        for benchmark, column in _QUALITY_COLUMNS.items():
            row[column] = scores[benchmark].get(slug, "") if slug else ""
        rows.append(row)
    return rows


def suggest_map(price_rows, aa_slugs, already_mapped):
    """Suggest an AA slug for each unmapped OpenRouter model (best-effort)."""
    norm_to_slug = {_norm(slug): slug for slug in aa_slugs}
    norm_keys = list(norm_to_slug)
    suggestions = []
    for row in price_rows:
        if row["id"] in already_mapped:
            continue
        match = difflib.get_close_matches(_norm(row["name"]), norm_keys, n=1, cutoff=0.8)
        suggestions.append({
            "openrouter_id": row["id"],
            "name": row["name"],
            "suggested_aa_slug": norm_to_slug[match[0]] if match else "",
        })
    return suggestions
