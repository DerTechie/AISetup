"""pricetrack CLI: snapshot prices, fetch scores, suggest map, join.

Run from the repo root: python -m pricing.pricetrack <command>
"""
import argparse
import csv
import datetime
import os

from pricing import csvio, join
from pricing.sources import artificialanalysis, openrouter

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PRICES_CSV = os.path.join(DATA_DIR, "openrouter-prices.csv")
AA_SCORES_CSV = os.path.join(DATA_DIR, "scores-artificialanalysis.csv")
ID_MAP_CSV = os.path.join(DATA_DIR, "model-id-map.csv")
PRICE_VS_QUALITY_CSV = os.path.join(DATA_DIR, "price-vs-quality.csv")


def _today():
    return datetime.date.today().isoformat()


def cmd_fetch_prices(_args):
    payload = openrouter.fetch()
    rows = openrouter.parse_models(payload, _today())
    csvio.write_csv(
        PRICES_CSV, openrouter.PRICE_FIELDNAMES, rows, sort_key=lambda r: r["id"]
    )
    print(f"Wrote {len(rows)} models to {PRICES_CSV}")


def cmd_fetch_scores(_args):
    payload = artificialanalysis.fetch()
    rows = artificialanalysis.parse_aa(payload, _today())
    csvio.write_csv(
        AA_SCORES_CSV,
        artificialanalysis.SCORE_FIELDNAMES,
        rows,
        sort_key=lambda r: (r["source_model_name"], r["benchmark"]),
    )
    print(f"Wrote {len(rows)} score rows to {AA_SCORES_CSV}")


def _read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def cmd_suggest_map(_args):
    prices = _read_rows(PRICES_CSV)
    scores = _read_rows(AA_SCORES_CSV)
    existing = _read_rows(ID_MAP_CSV)
    aa_slugs = sorted({s["source_model_name"] for s in scores})
    mapped = {m["openrouter_id"] for m in existing if m.get("aa_slug")}
    suggestions = join.suggest_map(prices, aa_slugs, mapped)
    matched = [s for s in suggestions if s["suggested_aa_slug"]]
    print(f"{len(matched)} suggested matches (of {len(suggestions)} unmapped):")
    for s in matched:
        print(f"  {s['openrouter_id']},{s['suggested_aa_slug']}    # {s['name']}")
    print("\nReview, then add the correct lines to", ID_MAP_CSV)


def cmd_join(_args):
    prices = _read_rows(PRICES_CSV)
    scores = _read_rows(AA_SCORES_CSV)
    id_map = _read_rows(ID_MAP_CSV)
    rows = join.build_price_vs_quality(prices, scores, id_map)
    csvio.write_csv(
        PRICE_VS_QUALITY_CSV, join.JOIN_FIELDNAMES, rows,
        sort_key=lambda r: r["openrouter_id"],
    )
    mapped = sum(1 for r in rows if r["aa_intelligence_index"])
    print(f"Wrote {len(rows)} rows ({mapped} with an intelligence score) to {PRICE_VS_QUALITY_CSV}")


def cmd_all(args):
    cmd_fetch_prices(args)
    cmd_fetch_scores(args)
    cmd_join(args)


def build_parser():
    parser = argparse.ArgumentParser(prog="pricetrack")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch-prices", help="snapshot OpenRouter prices").set_defaults(
        func=cmd_fetch_prices
    )
    sub.add_parser("fetch-scores", help="fetch Artificial Analysis scores").set_defaults(
        func=cmd_fetch_scores
    )
    sub.add_parser("suggest-map", help="suggest id-map entries (review by hand)").set_defaults(
        func=cmd_suggest_map
    )
    sub.add_parser("join", help="join prices + scores via id-map").set_defaults(
        func=cmd_join
    )
    sub.add_parser("all", help="fetch-prices, fetch-scores, then join").set_defaults(
        func=cmd_all
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
