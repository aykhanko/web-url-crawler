from __future__ import annotations

import asyncio
import logging
import time
from contextlib import AsyncExitStack
from pathlib import Path
from urllib.parse import urljoin

import aiohttp

from .config import Settings
from .exporter import export_results
from .filters import content_type, is_html, is_internal, looks_like_file
from .models import ExternalRecord, QueueItem, RedirectRecord, URLRecord, utc_now
from .normalizer import domain_folder, ensure_scheme, normalize_url, origin
from .parser import parse_html
from .playwright_renderer import PlaywrightRenderer
from .robots import RobotsPolicy, parse_robots
from .sitemap import decode_sitemap, parse_sitemap

LOGGER = logging.getLogger(__name__)
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class ResponseTooLargeError(RuntimeError):
    """Raised before a response can exceed the configured memory budget."""


class WebsiteCrawler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        raw_start = ensure_scheme(settings.start_url)
        normalized = normalize_url(
            raw_start, raw_start, keep_query_params=settings.keep_query_params
        )
        if normalized is None:
            raise ValueError(f"Invalid start URL: {settings.start_url!r}")
        self.start_url = normalized
        self.domain = domain_folder(normalized)
        self.output_dir = Path(settings.output_dir) / self.domain

        self.queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self.discovered: set[str] = set()
        self.enqueued: set[str] = set()
        self.visited: set[str] = set()
        self.pages: list[URLRecord] = []
        self.files: list[URLRecord] = []
        self.external: dict[str, ExternalRecord] = {}
        self.broken: list[URLRecord] = []
        self.redirects: list[RedirectRecord] = []

        self._redirect_keys: set[tuple[str, int, str]] = set()
        self._file_urls: set[str] = set()
        self._robots = RobotsPolicy(parser=None, sitemap_urls=())
        self._session: aiohttp.ClientSession | None = None
        self._renderer: PlaywrightRenderer | None = None
        self._stop = False
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._started_at = 0.0
        self._crawled_at = ""

    async def run(self) -> dict[str, object]:
        self._started_at = time.monotonic()
        self._crawled_at = utc_now()
        self._print_settings()
        timeout = aiohttp.ClientTimeout(total=self.settings.request_timeout)
        connector = aiohttp.TCPConnector(
            limit=self.settings.max_concurrency,
            ssl=self.settings.verify_ssl,
        )
        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
        }

        try:
            async with AsyncExitStack() as stack:
                self._session = await stack.enter_async_context(
                    aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers)
                )
                if self.settings.enable_playwright:
                    renderer = PlaywrightRenderer(
                        self.settings.request_timeout, self.settings.user_agent
                    )
                    self._renderer = await stack.enter_async_context(renderer)
                await self._bootstrap()
                workers = [
                    asyncio.create_task(self._worker(), name=f"crawler-worker-{index}")
                    for index in range(self.settings.max_concurrency)
                ]
                try:
                    await self.queue.join()
                except (KeyboardInterrupt, asyncio.CancelledError):
                    self._stop = True
                    LOGGER.warning("Shutdown requested; exporting partial results")
                finally:
                    for worker in workers:
                        worker.cancel()
                    await asyncio.gather(*workers, return_exceptions=True)
        finally:
            stats = self._stats()
            export_results(
                self.output_dir,
                internal_urls=self.discovered,
                pages=self.pages,
                files=self.files,
                external_urls=self.external.values(),
                broken_urls=self.broken,
                redirects=self.redirects,
                stats=stats,
                settings=self.settings.as_env(),
            )
            self._print_summary(stats, self.output_dir)
        return stats

    async def _bootstrap(self) -> None:
        await self._load_robots()
        # Reserve capacity for the explicitly requested entry point before a large
        # sitemap can fill MAX_PAGES.
        self._discover(self.start_url, self.start_url, 0, "start")
        if self.settings.enable_sitemap:
            sitemap_urls = list(self._robots.sitemap_urls)
            default_sitemap = urljoin(f"{origin(self.start_url)}/", "sitemap.xml")
            if default_sitemap not in sitemap_urls:
                sitemap_urls.append(default_sitemap)
            await self._load_sitemaps(sitemap_urls)

    async def _load_robots(self) -> None:
        robots_url = urljoin(f"{origin(self.start_url)}/", "robots.txt")
        try:
            status, _, data, _, _ = await self._request(robots_url)
            if status == 200:
                text = data.decode("utf-8", errors="replace")
                self._robots = parse_robots(
                    robots_url, text, self.settings.respect_robots_txt
                )
                LOGGER.info("robots.txt found")
            else:
                LOGGER.info("robots.txt not available (HTTP %s)", status)
        except (aiohttp.ClientError, asyncio.TimeoutError, ResponseTooLargeError) as exc:
            LOGGER.warning("Could not read robots.txt: %s", exc)

    async def _load_sitemaps(self, initial_urls: list[str]) -> None:
        pending = [(url, self.start_url) for url in initial_urls]
        seen: set[str] = set()
        while pending and not self._stop:
            raw_url, parent_url = pending.pop()
            sitemap_url = normalize_url(
                raw_url, parent_url, keep_query_params=self.settings.keep_query_params
            )
            if (
                not sitemap_url
                or sitemap_url in seen
                or not is_internal(
                    sitemap_url, self.start_url, self.settings.allow_subdomains
                )
            ):
                continue
            seen.add(sitemap_url)
            try:
                status, headers, data, final_url, _ = await self._request(sitemap_url)
                if status != 200:
                    continue
                decoded = decode_sitemap(
                    data, final_url, headers.get("Content-Encoding", "")
                )
                document = parse_sitemap(decoded)
                LOGGER.info("Sitemap found: %s", sitemap_url)
                pending.extend((child, final_url) for child in document.sitemaps)
                for url in document.urls:
                    self._discover(url, sitemap_url, 0, "sitemap")
            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                OSError,
                ValueError,
                ResponseTooLargeError,
            ) as exc:
                LOGGER.debug("Sitemap skipped (%s): %s", sitemap_url, exc)

    def _discover(self, raw_url: str, source_url: str, depth: int, kind: str) -> None:
        url = normalize_url(
            raw_url, source_url, keep_query_params=self.settings.keep_query_params
        )
        if url is None:
            return
        if not is_internal(url, self.start_url, self.settings.allow_subdomains):
            if url not in self.external:
                self.external[url] = ExternalRecord(url=url, source_url=source_url)
                LOGGER.info("[EXTERNAL] %s", url)
            return

        self.discovered.add(url)
        if looks_like_file(url):
            self._record_file_reference(url, source_url, depth)
            return
        if depth > self.settings.max_depth or url in self.enqueued:
            return
        if len(self.enqueued) >= self.settings.max_pages:
            return
        if not self._robots.allowed(self.settings.user_agent, url):
            LOGGER.debug("Blocked by robots.txt: %s", url)
            return
        self.enqueued.add(url)
        self.queue.put_nowait(QueueItem(url, source_url, depth, kind))

    def _record_file_reference(self, url: str, source_url: str, depth: int) -> None:
        if url in self._file_urls:
            return
        self._file_urls.add(url)
        self.files.append(URLRecord(url, None, "", source_url, depth, url, utc_now()))
        LOGGER.info("[FILE] %s", url)

    async def _worker(self) -> None:
        while True:
            item = await self.queue.get()
            try:
                if not self._stop and item.url not in self.visited:
                    self.visited.add(item.url)
                    await self._crawl(item)
            except Exception:
                LOGGER.exception("Unexpected crawl error for %s", item.url)
            finally:
                self.queue.task_done()

    async def _crawl(self, item: QueueItem) -> None:
        started = time.monotonic()
        try:
            status, headers, body, final_url, history = await self._request(
                item.url, read_non_html=False
            )
            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
            media_type = content_type(headers.get("Content-Type"))
            record = URLRecord(
                item.url,
                status,
                media_type,
                item.source_url,
                item.depth,
                final_url,
                item.discovered_at,
                elapsed_ms,
            )
            self._record_redirects(history)
            self._remember_final_url(final_url, item.url)
            if status >= 400:
                self.broken.append(record)
            if is_html(media_type):
                self.pages.append(record)
                LOGGER.info("[%s] %s", status, item.url)
                if status < 400:
                    await self._parse_and_discover(body, final_url, item.depth + 1)
            else:
                self._upsert_fetched_file(record)
                LOGGER.info("[FILE] %s", item.url)
        except (aiohttp.ClientError, asyncio.TimeoutError, ResponseTooLargeError) as exc:
            record = URLRecord(
                item.url,
                None,
                "",
                item.source_url,
                item.depth,
                item.url,
                item.discovered_at,
                round((time.monotonic() - started) * 1000, 2),
                str(exc),
            )
            self.broken.append(record)
            LOGGER.error("[ERROR] %s: %s", item.url, exc)

    async def _parse_and_discover(self, body: bytes, base_url: str, depth: int) -> None:
        parsed = parse_html(body)
        if not parsed.links and self._renderer is not None:
            try:
                parsed = parse_html(await self._renderer.render(base_url))
            except Exception as exc:
                LOGGER.warning("Playwright fallback failed for %s: %s", base_url, exc)
        for link in parsed.links:
            self._discover(link, base_url, depth, "html")
        if parsed.canonical:
            self._discover(parsed.canonical, base_url, depth, "canonical")

    def _upsert_fetched_file(self, record: URLRecord) -> None:
        self._file_urls.add(record.url)
        for index, existing in enumerate(self.files):
            if existing.url == record.url:
                self.files[index] = record
                return
        self.files.append(record)

    def _record_redirects(self, history: tuple[tuple[str, int, str], ...]) -> None:
        for source, status, destination in history:
            key = (source, status, destination)
            if key not in self._redirect_keys:
                self._redirect_keys.add(key)
                self.redirects.append(RedirectRecord(source, status, destination))

    def _remember_final_url(self, final_url: str, source_url: str) -> None:
        normalized = normalize_url(
            final_url,
            source_url,
            keep_query_params=self.settings.keep_query_params,
        )
        if normalized is None or normalized == source_url:
            return
        if is_internal(normalized, self.start_url, self.settings.allow_subdomains):
            self.discovered.add(normalized)
        elif normalized not in self.external:
            self.external[normalized] = ExternalRecord(
                url=normalized, source_url=source_url
            )

    async def _request(
        self, url: str, *, read_non_html: bool = True
    ) -> tuple[int, aiohttp.typedefs.LooseHeaders, bytes, str, tuple[tuple[str, int, str], ...]]:
        assert self._session is not None
        last_error: BaseException | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                await self._wait_for_rate_limit()
                async with self._session.get(
                    url, allow_redirects=True, max_redirects=10
                ) as response:
                    if response.status in RETRYABLE_STATUSES and attempt < self.settings.max_retries:
                        response.release()
                        await asyncio.sleep(self.settings.retry_backoff * (2**attempt))
                        continue
                    response_type = content_type(response.headers.get("Content-Type"))
                    if read_non_html or is_html(response_type):
                        body = await self._read_limited(response)
                    else:
                        body = b""
                        response.release()
                    history = tuple(
                        (
                            str(hop.url),
                            hop.status,
                            urljoin(str(hop.url), hop.headers.get("Location", "")),
                        )
                        for hop in response.history
                    )
                    return (
                        response.status,
                        response.headers,
                        body,
                        str(response.url),
                        history,
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt < self.settings.max_retries:
                    await asyncio.sleep(self.settings.retry_backoff * (2**attempt))
                    continue
                raise
        raise RuntimeError("Request retry loop exited unexpectedly") from last_error

    async def _read_limited(self, response: aiohttp.ClientResponse) -> bytes:
        declared = response.content_length
        if declared is not None and declared > self.settings.max_response_bytes:
            raise ResponseTooLargeError(
                f"response Content-Length {declared} exceeds MAX_RESPONSE_BYTES"
            )
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.content.iter_chunked(64 * 1024):
            total += len(chunk)
            if total > self.settings.max_response_bytes:
                raise ResponseTooLargeError("response body exceeds MAX_RESPONSE_BYTES")
            chunks.append(chunk)
        return b"".join(chunks)

    async def _wait_for_rate_limit(self) -> None:
        async with self._rate_lock:
            remaining = self.settings.crawl_delay - (
                time.monotonic() - self._last_request_at
            )
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._last_request_at = time.monotonic()

    def _stats(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "start_url": self.start_url,
            "crawled_at": self._crawled_at,
            "pages_crawled": len(self.pages),
            "urls_discovered": len(self.discovered),
            "files_found": len(self.files),
            "external_urls": len(self.external),
            "broken_urls": len(self.broken),
            "redirects": len(self.redirects),
            "duration_seconds": round(time.monotonic() - self._started_at, 2),
        }

    def _print_settings(self) -> None:
        print("================================")
        print(f"Starting crawl: {self.start_url}")
        print(f"Output folder: {self.output_dir}\n")
        print("Settings (.env):")
        env = self.settings.as_env()
        width = max(len(name) for name in env)
        for name, value in env.items():
            print(f"  {name.ljust(width)} = {value}")
        print("================================\n")

    @staticmethod
    def _print_summary(stats: dict[str, object], output_dir: Path) -> None:
        print("\n================================")
        print("Crawl completed\n")
        print(f"Pages crawled: {stats['pages_crawled']}")
        print(f"URLs discovered: {stats['urls_discovered']}")
        print(f"Files: {stats['files_found']}")
        print(f"External URLs: {stats['external_urls']}")
        print(f"Broken URLs: {stats['broken_urls']}")
        print(f"Redirects: {stats['redirects']}")
        print(f"Duration: {stats['duration_seconds']} sec")
        print(f"Saved to: {output_dir}")
        print("================================")
