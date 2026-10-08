from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

from .models import ExternalRecord, RedirectRecord, URLRecord

URL_FIELDS = ["url", "type", "status_code", "content_type", "depth", "source_url"]
ISSUE_FIELDS = ["issue", "url", "status_code", "detail", "source_url"]


def export_results(
    output_dir: str | Path,
    *,
    internal_urls: Iterable[str],
    pages: Iterable[URLRecord],
    files: Iterable[URLRecord],
    external_urls: Iterable[ExternalRecord],
    broken_urls: Iterable[URLRecord],
    redirects: Iterable[RedirectRecord],
    stats: dict[str, object],
    settings: dict[str, object] | None = None,
) -> Path:
    """Write a compact per-domain result set: urls.csv, issues.csv, summary.json."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    broken_list = list(broken_urls)

    _write_csv(
        directory / "urls.csv",
        _url_rows(internal_urls, pages, files, broken_list),
        URL_FIELDS,
    )
    _write_csv(directory / "issues.csv", _issue_rows(broken_list, redirects), ISSUE_FIELDS)

    external_domains = Counter(
        urlsplit(record.url).hostname or record.url for record in external_urls
    )
    summary = {
        "stats": stats,
        "settings": settings or {},
        "external_domains": dict(external_domains.most_common()),
    }
    with (directory / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return directory


def _url_rows(
    internal_urls: Iterable[str],
    pages: Iterable[URLRecord],
    files: Iterable[URLRecord],
    broken: list[URLRecord],
) -> list[dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for kind, records in (("page", pages), ("file", files), ("page", broken)):
        for record in records:
            rows.setdefault(record.url, _url_row(record, kind))
    for url in internal_urls:
        rows.setdefault(
            url,
            {"url": url, "type": "not_crawled", "status_code": "", "content_type": "",
             "depth": "", "source_url": ""},
        )
    return [rows[url] for url in sorted(rows)]


def _url_row(record: URLRecord, kind: str) -> dict[str, object]:
    return {
        "url": record.url,
        "type": kind,
        "status_code": record.status_code if record.status_code is not None else "",
        "content_type": record.content_type,
        "depth": record.depth,
        "source_url": record.source_url,
    }


def _issue_rows(
    broken: Iterable[URLRecord], redirects: Iterable[RedirectRecord]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in broken:
        rows.append({
            "issue": "broken" if record.status_code is not None else "error",
            "url": record.url,
            "status_code": record.status_code if record.status_code is not None else "",
            "detail": record.error,
            "source_url": record.source_url,
        })
    for redirect in redirects:
        rows.append({
            "issue": "redirect",
            "url": redirect.source_url,
            "status_code": redirect.status_code,
            "detail": redirect.destination_url,
            "source_url": "",
        })
    return rows


def _write_csv(path: Path, rows: Iterable[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
