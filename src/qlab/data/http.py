"""Small HTTP helper with retries and an honest user agent."""

from __future__ import annotations

import time

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; quant-lab/0.1; personal research; +https://github.com/discoenmarte/quant-lab)"


class SourceError(RuntimeError):
    """A data source could not be reached or returned something unusable."""


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
            res = httpx.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
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
