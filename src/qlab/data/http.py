"""Small HTTP helper with retries, an honest user agent and one shared keep-alive connection pool.

Reusing connections matters for archives of many small files: from the owner's network a fresh TLS connection
to the Binance archive costs about half a second, longer than the download itself.
"""

from __future__ import annotations

import threading
import time

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; quant-lab/0.1; personal research; +https://github.com/discoenmarte/quant-lab)"


class SourceError(RuntimeError):
    """A data source could not be reached or returned something unusable."""


_client: httpx.Client | None = None
_client_lock = threading.Lock()


def _shared() -> httpx.Client:
    """The process-wide client, created on first use; httpx clients are safe to share between threads.

    Returns:
        A client with the lab's user agent that follows redirects.
    """
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True,
                                   limits=httpx.Limits(max_connections=16, max_keepalive_connections=16))
        return _client


def get(url: str, *, timeout: float = 30.0, retries: int = 3, allow_404: bool = False) -> bytes | None:
    """Download a URL.

    Network errors, rate limits (429) and server errors (5xx) are retried after 1, 2, 4... seconds. Other
    client errors fail at once.

    Args:
        url: Address to fetch.
        timeout: Seconds per attempt.
        retries: Attempts before giving up.
        allow_404: Return None on 404 instead of raising (archives often lack the newest files).

    Returns:
        The response body, or None for a 404 when ``allow_404`` is set.

    Raises:
        SourceError: The server answered with a non-retryable error, or every attempt failed.
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            res = _shared().get(url, timeout=timeout)
        except httpx.HTTPError as err:
            last = err
            time.sleep(2**attempt)
            continue
        if res.status_code == 404 and allow_404:
            return None
        if res.status_code == 429 or res.status_code >= 500:
            last = SourceError(f"HTTP {res.status_code} from {url}")
            time.sleep(2**attempt)
            continue
        if res.status_code >= 400:
            raise SourceError(f"HTTP {res.status_code} from {url}")
        return res.content
    raise SourceError(f"giving up on {url}: {last}") from last
