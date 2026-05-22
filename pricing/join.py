"""Join OpenRouter prices with Artificial Analysis scores via the id-map."""
import difflib
import re

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(text):
    """Lowercase and strip vendor prefix + non-alphanumerics for matching."""
    text = text.split(":", 1)[-1]  # drop "Vendor: " prefix if present
    return _NORM_RE.sub("", text.lower())


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
