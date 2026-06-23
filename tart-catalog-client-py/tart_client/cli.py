#!/usr/bin/env python3
"""
CLI entry points for the TART catalogue client.

Usage:
    uv run python -m tart_client.cli ecef [--date DATE] [--url URL]
    uv run python -m tart_client.cli celestial [--date DATE] [--url URL]
"""

import argparse
import json
import os
import sys

from tart_client import CatalogueClient


def cmd_ecef(args):
    client = CatalogueClient(base_url=args.url)
    results = client.ecef_positions(dt=args.date)
    print(json.dumps(results, indent=2))


def cmd_celestial(args):
    client = CatalogueClient(base_url=args.url)
    results = client.celestial_positions(dt=args.date)
    print(json.dumps(results, indent=2))


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

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
