"""pricetrack CLI: snapshot prices, fetch scores, suggest map, join.

Run from the repo root: python -m pricing.pricetrack <command>
"""
import argparse
import datetime
import os

from pricing import csvio
from pricing.sources import openrouter

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PRICES_CSV = os.path.join(DATA_DIR, "openrouter-prices.csv")


def _today():
    return datetime.date.today().isoformat()


def cmd_fetch_prices(_args):
    payload = openrouter.fetch()
    rows = openrouter.parse_models(payload, _today())
    csvio.write_csv(
        PRICES_CSV, openrouter.PRICE_FIELDNAMES, rows, sort_key=lambda r: r["id"]
    )
    print(f"Wrote {len(rows)} models to {PRICES_CSV}")


def build_parser():
    parser = argparse.ArgumentParser(prog="pricetrack")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch-prices", help="snapshot OpenRouter prices").set_defaults(
        func=cmd_fetch_prices
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
