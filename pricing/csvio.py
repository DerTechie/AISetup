"""Deterministic, atomic CSV writing and price-unit formatting."""
import csv
import os
import tempfile
from decimal import Decimal

_PER_MILLION = Decimal(1_000_000)


def fmt_mtok(per_token):
    """OpenRouter per-token USD string -> USD per 1M tokens.

    None -> "" (field absent). "0"/"0.0" -> "0" (genuinely free).
    Lossless via Decimal; plain decimal notation, no trailing-zero noise.
    """
    if per_token is None or per_token == "":
        return ""
    value = (Decimal(str(per_token)) * _PER_MILLION).normalize()
    # `:f` renders plain decimal (no scientific notation); normalize drops trailing zeros.
    return f"{value:f}"


def write_csv(path, fieldnames, rows, sort_key):
    """Write rows sorted by sort_key, atomically (temp file + os.replace)."""
    ordered = sorted(rows, key=sort_key)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(ordered)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
