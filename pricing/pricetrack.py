"""pricetrack CLI: snapshot prices, fetch scores, suggest map, join.

Run from the repo root: python -m pricing.pricetrack <command>
"""
import argparse
import datetime
import os

from pricing import csvio
from pricing.sources import artificialanalysis, openrouter

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PRICES_CSV = os.path.join(DATA_DIR, "openrouter-prices.csv")
AA_SCORES_CSV = os.path.join(DATA_DIR, "scores-artificialanalysis.csv")


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


def build_parser():
    parser = argparse.ArgumentParser(prog="pricetrack")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch-prices", help="snapshot OpenRouter prices").set_defaults(
        func=cmd_fetch_prices
    )
    sub.add_parser("fetch-scores", help="fetch Artificial Analysis scores").set_defaults(
        func=cmd_fetch_scores
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
