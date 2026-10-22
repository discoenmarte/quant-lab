"""One collection pass over the radar's sources: the quick pass every 15 minutes, the deep pass once a day.

- **Quick** (``quick``): prediction-market snapshots, Hacker News for the last days, and the stress files when their
  cached copy is older than six hours. Cheap and polite enough to run every 15 minutes.
- **Deep** (``deep``): everything, with history: Wikipedia views, GDELT timelines and headlines (GDELT is too strict
  for the quick pass), Hacker News for the last week, market history, the stress files fresh.
- **One pass at a time.** Passes run inside the Lab API on the server (DuckDB has one writer), and a quick pass that
  starts while the deep pass is still running is skipped rather than queued.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .. import audit, db
from ..timeutil import naive_utc, now_utc
from . import store, watchlist
from .sources import gdelt, hn, markets, stress, wiki

COLLECTORS: dict[str, tuple[Callable[..., dict[str, Any]], str]] = {
    "wiki": (wiki.collect, "wiki"),
    "gdelt": (gdelt.collect, "gdelt"),
    "hn": (hn.collect, "hn"),
    "markets": (markets.collect, "scenarios"),
    "stress": (stress.collect, "stress"),
}
QUICK = ("markets", "hn", "stress")
DEEP = ("stress", "markets", "hn", "wiki", "gdelt")
_lock = threading.Lock()


def run(kind: str = "quick", only: list[str] | None = None, fetcher: store.Fetcher | None = None,
        now: datetime | None = None, log: Callable[[str], None] | None = None, actor: str = "cli") -> dict[str, Any]:
    """Collect from the sources of a pass.

    Args:
        kind: ``quick`` or ``deep``.
        only: Collect only these sources (names in ``COLLECTORS``); None means the pass's list.
        fetcher: The HTTP fetcher (tests pass one with a mock transport); None makes one.
        now: Current time (tz-aware); None means now.
        log: Receives progress lines.
        actor: Who ran it, for the audit log.

    Returns:
        ``run_id, kind, started_at, finished_at, ok`` (every source ok), ``skipped`` (another pass was running) and
        ``sources`` (each collector's summary).

    Raises:
        ValueError: Unknown pass or source.
    """
    if kind not in ("quick", "deep"):
        raise ValueError("kind must be quick or deep")
    names = list(only) if only else list(QUICK if kind == "quick" else DEEP)
    unknown = [n for n in names if n not in COLLECTORS]
    if unknown:
        raise ValueError(f"unknown radar sources {unknown}; choose from {sorted(COLLECTORS)}")
    started = now or now_utc()
    run_id = uuid.uuid4().hex[:12]
    if not _lock.acquire(blocking=False):
        return {"run_id": run_id, "kind": kind, "started_at": started.isoformat(timespec="seconds"),
                "finished_at": None, "ok": True, "skipped": True, "sources": []}
    try:
        wl = watchlist.load()
        fetch = fetcher or store.Fetcher()
        results = []
        for name in names:
            fn, section = COLLECTORS[name]
            try:
                results.append(fn(wl[section], fetch, started, kind == "deep", log))
            except Exception as err:  # a collector bug must not stop the pass
                store.mark_source(name, False, f"collector failed: {err}", started)
                results.append({"source": name, "ok": False, "series": 0, "rows": 0, "errors": [str(err)],
                                "message": f"collector failed: {err}"})
        finished = now_utc() if now is None else now
        out = {"run_id": run_id, "kind": kind, "started_at": started.isoformat(timespec="seconds"),
               "finished_at": finished.isoformat(timespec="seconds"), "ok": all(r["ok"] for r in results),
               "skipped": False, "sources": results}
        with db.connect() as con:
            con.execute("INSERT INTO radar_runs (run_id, started_at, finished_at, kind, ok, summary) "
                        "VALUES (?, ?, ?, ?, ?, ?)", [run_id, naive_utc(started), naive_utc(finished),
                                                      f"collect-{kind}", out["ok"], db.dumps(out)])
        audit.log("radar.collect", target=run_id, actor=actor,
                  details={"kind": kind, "sources": {r["source"]: r["message"] for r in results}})
        return out
    finally:
        _lock.release()
