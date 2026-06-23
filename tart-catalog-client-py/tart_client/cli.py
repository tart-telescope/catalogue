#!/usr/bin/env python3
"""
CLI entry points for the TART catalogue client.

Usage:
    uv run python -m tart_client.cli ecef [--date DATE] [--url URL]
    uv run python -m tart_client.cli celestial [--date DATE] [--url URL]
    uv run python -m tart_client.cli benchmark [--url URL]
"""

import argparse
import datetime
import json
import os
import sys
import time

from tart_client import CatalogueClient


def cmd_ecef(args):
    client = CatalogueClient(base_url=args.url)
    results = client.ecef_positions(dt=args.date)
    print(json.dumps(results, indent=2))


def cmd_celestial(args):
    client = CatalogueClient(base_url=args.url)
    results = client.celestial_positions(dt=args.date)
    print(json.dumps(results, indent=2))


def cmd_benchmark(args):
    N = 10_000
    client = CatalogueClient(base_url=args.url)

    now = datetime.datetime.now(datetime.timezone.utc)
    week_ago = now - datetime.timedelta(days=7)
    step = (now - week_ago) / N

    print(f"Benchmark: {N} celestial position queries over the last week")
    print(f"Server: {client.base_url}")

    t0 = time.perf_counter()
    total_positions = 0
    for i in range(N):
        dt = week_ago + step * i
        results = client.celestial_positions(dt=dt)
        total_positions += len(results)
    elapsed = time.perf_counter() - t0

    print(f"Total time:       {elapsed:.2f} s")
    print(f"Positions:        {total_positions}")
    print(f"Positions/sec:    {total_positions / elapsed:,.0f}")
    print(f"Queries/sec:      {N / elapsed:,.1f}")
    print(f"Avg query time:   {elapsed / N * 1000:.1f} ms")


def main():
    parser = argparse.ArgumentParser(description="TART Catalogue client")
    parser.add_argument(
        "--url",
        default=os.environ.get("TART_CATALOGUE_URL", "https://tart.elec.ac.nz/catalog"),
        help="Catalogue server URL",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="UTC date in ISO format (default: now)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_ecef = sub.add_parser("ecef", help="Return ECEF positions")
    p_ecef.set_defaults(func=cmd_ecef)

    p_cel = sub.add_parser("celestial", help="Return celestial (RA/Dec) positions")
    p_cel.set_defaults(func=cmd_celestial)

    p_bench = sub.add_parser(
        "benchmark", help="Benchmark 10,000 celestial position queries"
    )
    p_bench.set_defaults(func=cmd_benchmark)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
