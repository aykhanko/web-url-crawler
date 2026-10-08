from __future__ import annotations

import csv
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from crawler.config import Settings
from crawler.crawler import WebsiteCrawler


class SiteHandler(BaseHTTPRequestHandler):
    base_url = ""

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = self.path.split("?", 1)[0]
        if path == "/robots.txt":
            self._send(
                200,
                f"User-agent: *\nDisallow: /private\nSitemap: {self.base_url}/index.xml\n",
                "text/plain",
            )
        elif path == "/index.xml":
            self._send(
                200,
                "<sitemapindex><sitemap><loc>/nested.xml</loc></sitemap></sitemapindex>",
                "application/xml",
            )
        elif path == "/nested.xml":
            self._send(
                200,
                f"<urlset><url><loc>{self.base_url}/from-sitemap</loc></url></urlset>",
                "application/xml",
            )
        elif path == "/sitemap.xml":
            self._send(404, "missing", "text/plain")
        elif path == "/":
            self._send(
                200,
                """<html><body>
                <a href='/about/'>About</a><a href='/about?copy=1#x'>Duplicate</a>
                <a href='/redirect'>Redirect</a><a href='/broken'>Broken</a>
                <a href='/private/hidden'>Private</a><a href='/report.pdf'>PDF</a>
                <a href='https://outside.test/x'>External</a>
                <a href='mailto:a@example.com'>Mail</a>
                </body></html>""",
                "text/html; charset=utf-8",
            )
        elif path in {"/about", "/from-sitemap", "/final"}:
            self._send(200, "<html><body>done</body></html>", "text/html")
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.end_headers()
        elif path == "/broken":
            self._send(404, "<html>no</html>", "text/html")
        else:
            self._send(404, "no", "text/plain")

    def _send(self, status: int, body: str, media_type: str) -> None:
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_: object) -> None:
        pass


class CrawlerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), SiteHandler)
        host, port = self.server.server_address
        SiteHandler.base_url = f"http://{host}:{port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.temp_dir = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    async def test_full_discovery_and_exports(self) -> None:
        settings = Settings(
            start_url=SiteHandler.base_url,
            max_pages=20,
            max_depth=5,
            max_concurrency=3,
            crawl_delay=0,
            max_retries=0,
            output_dir=self.temp_dir.name,
        )
        crawler = WebsiteCrawler(settings)
        stats = await crawler.run()

        self.assertIn(f"{SiteHandler.base_url}/about", crawler.discovered)
        self.assertIn(f"{SiteHandler.base_url}/from-sitemap", crawler.discovered)
        self.assertIn(f"{SiteHandler.base_url}/final", crawler.discovered)
        self.assertIn(f"{SiteHandler.base_url}/private/hidden", crawler.discovered)
        self.assertNotIn(f"{SiteHandler.base_url}/private/hidden", crawler.visited)
        self.assertEqual(len([url for url in crawler.discovered if "/about" in url]), 1)
        self.assertIn(f"{SiteHandler.base_url}/report.pdf", {item.url for item in crawler.files})
        self.assertIn("https://outside.test/x", crawler.external)
        self.assertEqual(stats["broken_urls"], 1)
        self.assertEqual(stats["redirects"], 1)

        port = SiteHandler.base_url.rsplit(":", 1)[1]
        output = Path(self.temp_dir.name) / f"127.0.0.1_{port}"
        self.assertEqual(crawler.output_dir, output)
        self.assertEqual(
            sorted(path.name for path in output.iterdir()),
            ["issues.csv", "summary.json", "urls.csv"],
        )
        summary = json.loads((output / "summary.json").read_text())
        self.assertEqual(summary["stats"]["start_url"], f"{SiteHandler.base_url}/")
        self.assertEqual(summary["settings"]["MAX_PAGES"], 20)
        self.assertEqual(summary["external_domains"], {"outside.test": 1})

        with (output / "urls.csv").open(encoding="utf-8") as handle:
            url_rows = {row["url"]: row for row in csv.DictReader(handle)}
        self.assertEqual(url_rows[f"{SiteHandler.base_url}/report.pdf"]["type"], "file")
        self.assertEqual(url_rows[f"{SiteHandler.base_url}/private/hidden"]["type"], "not_crawled")
        with (output / "issues.csv").open(encoding="utf-8") as handle:
            issues = sorted(row["issue"] for row in csv.DictReader(handle))
        self.assertEqual(issues, ["broken", "redirect"])


if __name__ == "__main__":
    unittest.main()
