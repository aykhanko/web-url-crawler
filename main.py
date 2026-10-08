from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import replace

from crawler.config import Settings, SettingsError
from crawler.crawler import WebsiteCrawler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Discover internal URLs, files, redirects and broken links on a website. "
            "All settings except the URL are read from .env."
        )
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="Start URL. If omitted, you are asked in the terminal (Enter uses START_URL from .env)",
    )
    return parser


def ask_url(default: str) -> str:
    """Prompt for the start URL; an empty answer falls back to START_URL."""
    if not sys.stdin.isatty():
        return default
    hint = f" [{default}]" if default else ""
    try:
        answer = input(f"URL to crawl{hint}: ").strip()
    except EOFError:
        return default
    return answer or default


async def async_main() -> int:
    args = build_parser().parse_args()
    try:
        settings = Settings.from_env()
        url = args.url or ask_url(settings.start_url)
        settings = replace(settings, start_url=url.strip())
        settings.validate()
        crawler = WebsiteCrawler(settings)
    except (SettingsError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        await crawler.run()
        return 0
    except RuntimeError as exc:
        print(f"Crawler error: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    try:
        return asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\nCrawler stopped.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
