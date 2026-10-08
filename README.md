# Website URL Crawler

Production-oriented, asynchronous Python crawler that discovers a site's internal
pages through HTML links, `robots.txt`, regular sitemaps, sitemap indexes, nested
sitemaps, and gzip-compressed sitemaps. It records pages, files, external links,
broken URLs, and redirects as separate machine-readable outputs.

## Architecture

The CLI loads validated settings and starts `WebsiteCrawler`. URL normalization
and domain/file filters are pure helper modules. Robots, HTML, and sitemap parsers
only parse their respective formats. The crawler owns the bounded-concurrency
queue, retries, rate limiting, and crawl state; the exporter owns all filesystem
output. Playwright is isolated behind a lazy optional renderer.

```text
main.py
  -> config.py
  -> crawler.py
       -> normalizer.py / filters.py
       -> robots.py / sitemap.py / parser.py
       -> playwright_renderer.py (optional)
       -> exporter.py
```

## Features

- Async crawling with configurable concurrency, timeout, retries, exponential backoff, and delay
- Internal-domain boundary with optional subdomain crawling
- Relative/absolute URL resolution, fragment removal, trailing-slash deduplication, and optional query retention
- `robots.txt` rules and `Sitemap:` directive support
- `<urlset>`, `<sitemapindex>`, nested sitemap, and `.xml.gz` support
- Redirect chains, canonical links, HTTP errors, request errors, and response-time capture
- HTML-only link extraction; known files are recorded without being downloaded
- Configurable response-size guard and SSL verification enabled by default
- Optional Playwright fallback when an HTML response contains no ordinary links
- Partial-result export during graceful shutdown

## Requirements

- Python 3.11 or newer
- Network access to the target website
- Playwright and Chromium only when JavaScript rendering is enabled

## Installation

```bash
git clone <repository-url>
cd website-url-crawler

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell activation and environment-file copy:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` if desired, then run:

```bash
python main.py https://example.com
```

## Usage

Only the URL comes from the terminal; every other setting is read from `.env`.
Without a positional URL the crawler asks for one interactively — pressing Enter
uses `START_URL` from `.env`. When the crawl starts, the active `.env` settings
and the output folder are printed.

```bash
python main.py                       # prompts: URL to crawl [https://example.com]:
python main.py https://example.com   # skips the prompt
python main.py --help
```

Run the included unit and local integration tests without contacting a public site:

```bash
python -m unittest discover -v
```

## Output files

Each domain gets its own folder under `OUTPUT_DIR` (`www.` is dropped, a
non-default port is appended as `_port`). Re-crawling a domain overwrites only
that domain's folder.

```text
output/
├── example.com/
│   ├── urls.csv       # every internal URL: url, type, status_code, content_type, depth, source_url
│   ├── issues.csv     # problems to fix: issue, url, status_code, detail, source_url
│   └── summary.json   # stats, .env settings used, external domains with link counts
└── another-site.org/
    └── ...
```

- `urls.csv` `type`: `page` (HTML), `file` (non-HTML/file link), `not_crawled`
  (discovered but skipped, e.g. robots.txt, depth or `MAX_PAGES` limits).
- `issues.csv` `issue`: `broken` (HTTP 400+), `error` (network/timeout, message in
  `detail`), `redirect` (destination URL in `detail`).

## Configuration

| Variable | Default | Meaning |
|---|---:|---|
| `START_URL` | `https://example.com` | Default answer for the URL prompt |
| `REQUEST_TIMEOUT` | `15` | Total request timeout in seconds |
| `MAX_RETRIES` | `3` | Retries after network or retryable HTTP errors |
| `RETRY_BACKOFF` | `0.5` | Base exponential retry delay in seconds |
| `MAX_PAGES` | `10000` | Maximum URLs scheduled for HTTP requests |
| `MAX_DEPTH` | `20` | Maximum HTML link depth |
| `MAX_CONCURRENCY` | `5` | Concurrent crawler workers/connections |
| `CRAWL_DELAY` | `0.1` | Global minimum interval between requests |
| `USER_AGENT` | `WebsiteURLCrawler/1.0` | HTTP and robots user agent |
| `ALLOW_SUBDOMAINS` | `false` | Allow hosts below the starting hostname |
| `KEEP_QUERY_PARAMS` | `false` | Preserve query strings during normalization |
| `RESPECT_ROBOTS_TXT` | `true` | Apply robots allow/disallow rules |
| `ENABLE_SITEMAP` | `true` | Discover and recursively parse sitemaps |
| `ENABLE_PLAYWRIGHT` | `false` | Render linkless HTML with Chromium |
| `VERIFY_SSL` | `true` | Verify TLS certificates |
| `MAX_RESPONSE_BYTES` | `5000000` | Maximum decompressed body read per response |
| `OUTPUT_DIR` | `output` | Parent directory; results go to `OUTPUT_DIR/<domain>/` |

The delay is global rather than per worker, keeping aggregate request rate polite.
robots.txt is always checked for sitemap directives; its crawl rules are enforced
only when `RESPECT_ROBOTS_TXT=true`.

## Optional Playwright installation

Playwright is deliberately absent from the core dependencies. Install it only if
the target requires JavaScript rendering:

```bash
pip install "playwright>=1.46,<2.0"
playwright install chromium
```

Then set `ENABLE_PLAYWRIGHT=true`. The fallback runs only on successful HTML pages
where the normal response yields no `<a href>` links.

## Known limitations

- The crawler does not execute forms, click buttons, authenticate, or bypass bot protection.
- Query removal can merge semantically distinct URLs; enable `KEEP_QUERY_PARAMS` when necessary.
- File-like URLs with no recognizable extension require one HTTP request before their content type is known.
- Sitemap loading is sequential and shares the global rate limiter to remain polite.
- robots rules are evaluated using Python's standard `urllib.robotparser`; non-standard directives may differ from a search engine's interpretation.
- Very large pages/sitemaps are rejected by `MAX_RESPONSE_BYTES` and recorded only when they are part of the normal crawl queue.
