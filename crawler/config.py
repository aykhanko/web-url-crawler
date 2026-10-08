from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class SettingsError(ValueError):
    """Raised when crawler configuration is invalid."""


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{name} must be true or false, got {value!r}")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise SettingsError(f"{name} must be a number") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    start_url: str = ""
    request_timeout: float = 15.0
    max_retries: int = 3
    retry_backoff: float = 0.5
    max_pages: int = 10_000
    max_depth: int = 20
    max_concurrency: int = 5
    crawl_delay: float = 0.1
    user_agent: str = "WebsiteURLCrawler/1.0"
    allow_subdomains: bool = False
    keep_query_params: bool = False
    respect_robots_txt: bool = True
    enable_sitemap: bool = True
    enable_playwright: bool = False
    verify_ssl: bool = True
    max_response_bytes: int = 5_000_000
    output_dir: str = "output"

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> "Settings":
        load_dotenv(dotenv_path=env_file, override=False)
        return cls(
            start_url=os.getenv("START_URL", "").strip(),
            request_timeout=_float("REQUEST_TIMEOUT", 15.0),
            max_retries=_int("MAX_RETRIES", 3),
            retry_backoff=_float("RETRY_BACKOFF", 0.5),
            max_pages=_int("MAX_PAGES", 10_000),
            max_depth=_int("MAX_DEPTH", 20),
            max_concurrency=_int("MAX_CONCURRENCY", 5),
            crawl_delay=_float("CRAWL_DELAY", 0.1),
            user_agent=os.getenv("USER_AGENT", "WebsiteURLCrawler/1.0").strip(),
            allow_subdomains=_bool("ALLOW_SUBDOMAINS", False),
            keep_query_params=_bool("KEEP_QUERY_PARAMS", False),
            respect_robots_txt=_bool("RESPECT_ROBOTS_TXT", True),
            enable_sitemap=_bool("ENABLE_SITEMAP", True),
            enable_playwright=_bool("ENABLE_PLAYWRIGHT", False),
            verify_ssl=_bool("VERIFY_SSL", True),
            max_response_bytes=_int("MAX_RESPONSE_BYTES", 5_000_000),
            output_dir=os.getenv("OUTPUT_DIR", "output").strip(),
        )

    def as_env(self) -> dict[str, object]:
        """Active settings keyed by their .env variable names (START_URL excluded)."""
        return {
            "REQUEST_TIMEOUT": self.request_timeout,
            "MAX_RETRIES": self.max_retries,
            "RETRY_BACKOFF": self.retry_backoff,
            "MAX_PAGES": self.max_pages,
            "MAX_DEPTH": self.max_depth,
            "MAX_CONCURRENCY": self.max_concurrency,
            "CRAWL_DELAY": self.crawl_delay,
            "USER_AGENT": self.user_agent,
            "ALLOW_SUBDOMAINS": self.allow_subdomains,
            "KEEP_QUERY_PARAMS": self.keep_query_params,
            "RESPECT_ROBOTS_TXT": self.respect_robots_txt,
            "ENABLE_SITEMAP": self.enable_sitemap,
            "ENABLE_PLAYWRIGHT": self.enable_playwright,
            "VERIFY_SSL": self.verify_ssl,
            "MAX_RESPONSE_BYTES": self.max_response_bytes,
            "OUTPUT_DIR": self.output_dir,
        }

    def validate(self) -> None:
        if not self.start_url:
            raise SettingsError("provide a URL argument or set START_URL in .env")
        positive = {
            "REQUEST_TIMEOUT": self.request_timeout,
            "MAX_PAGES": self.max_pages,
            "MAX_CONCURRENCY": self.max_concurrency,
            "MAX_RESPONSE_BYTES": self.max_response_bytes,
        }
        for name, value in positive.items():
            if value <= 0:
                raise SettingsError(f"{name} must be greater than zero")
        if self.max_retries < 0 or self.max_depth < 0:
            raise SettingsError("MAX_RETRIES and MAX_DEPTH cannot be negative")
        if self.crawl_delay < 0 or self.retry_backoff < 0:
            raise SettingsError("CRAWL_DELAY and RETRY_BACKOFF cannot be negative")
