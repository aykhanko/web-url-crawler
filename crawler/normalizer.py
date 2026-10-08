from __future__ import annotations

import posixpath
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

IGNORED_SCHEMES = {"mailto", "tel", "javascript", "data", "blob", "file"}
_DEFAULT_PORTS = {"http": 80, "https": 443}
_MULTI_SLASH = re.compile(r"/{2,}")


def ensure_scheme(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    parsed = urlsplit(value)
    return value if parsed.scheme else f"https://{value}"


def normalize_url(
    raw_url: str | None,
    base_url: str,
    *,
    keep_query_params: bool,
) -> str | None:
    if not raw_url:
        return None
    candidate = raw_url.strip()
    if not candidate or candidate.startswith("#"):
        return None
    scheme_hint = candidate.split(":", 1)[0].lower() if ":" in candidate else ""
    if scheme_hint in IGNORED_SCHEMES:
        return None

    try:
        parsed = urlsplit(urljoin(base_url, candidate))
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            return None
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port
        netloc = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
        if port and port != _DEFAULT_PORTS.get(scheme):
            netloc = f"{netloc}:{port}"
        path = _normalize_path(parsed.path)
        query = parsed.query if keep_query_params else ""
        return urlunsplit((scheme, netloc, path, query, ""))
    except (UnicodeError, ValueError):
        return None


def _normalize_path(path: str) -> str:
    if not path or path == "/":
        return "/"
    normalized = posixpath.normpath(_MULTI_SLASH.sub("/", path))
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized.rstrip("/") or "/"


def domain_folder(url: str) -> str:
    """Filesystem-safe folder name for a URL's host, e.g. ``example.com``."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "unknown").lower().removeprefix("www.")
    name = f"{host}_{parsed.port}" if parsed.port else host
    return re.sub(r"[^a-z0-9._-]", "_", name)


def origin(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
