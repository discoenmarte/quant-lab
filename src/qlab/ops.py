"""Operations on a server: database snapshots, and what the machine has left.

Design (decision 0012):

- **Snapshots from inside the writer.** Copying ``lab.duckdb`` while ``qlab serve`` writes to it can give a broken
  copy, so a snapshot is taken through the database itself (``COPY FROM DATABASE``, one consistent transaction), by
  the process that holds it: through the API when the server runs, here otherwise.
- **A few local copies, the rest elsewhere.** ``keep`` snapshots stay in ``<data>/backups``; the server's own
  provider backups (Hetzner keeps seven daily images) and ``make pull`` on the owner's Mac carry them off the machine.
- Nothing here touches research results: a snapshot is a copy, and restoring one is a manual step on purpose.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from . import audit, config, db
from .timeutil import now_utc


def backups_dir() -> Path:
    """Where snapshots are kept.

    Returns:
        ``<data>/backups``.
    """
    return config.data_dir() / "backups"


def backup(keep: int = 3, actor: str = "user") -> dict[str, Any]:
    """Write a consistent snapshot of the lab database and drop the oldest beyond ``keep``.

    Args:
        keep: Snapshots to keep, newest first (at least 1).
        actor: Who asked, for the audit log.

    Returns:
        ``path``, ``bytes``, ``seconds`` and ``removed`` (older snapshots deleted).
    """
    out_dir = backups_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    started = now_utc()
    dest = out_dir / f"lab-{started.strftime('%Y%m%dT%H%M%S')}.duckdb"
    tmp = dest.with_suffix(".partial")
    tmp.unlink(missing_ok=True)
    with db.connect() as con:
        name = con.execute("SELECT current_database()").fetchone()[0]
        con.execute(f"ATTACH '{tmp}' AS qlab_snapshot")
        try:
            con.execute(f'COPY FROM DATABASE "{name}" TO qlab_snapshot')
        finally:
            con.execute("DETACH qlab_snapshot")
    tmp.rename(dest)
    snaps = sorted(out_dir.glob("lab-*.duckdb"), reverse=True)
    removed = []
    for old in snaps[max(1, keep):]:
        old.unlink(missing_ok=True)
        removed.append(old.name)
    seconds = (now_utc() - started).total_seconds()
    audit.log("ops.backup", target=dest.name, actor=actor, details={"bytes": dest.stat().st_size, "removed": removed})
    return {"path": str(dest), "bytes": dest.stat().st_size, "seconds": round(seconds, 1), "removed": removed}


def machine() -> dict[str, Any]:
    """Disk and memory left on the machine, for the System page and the bot.

    Returns:
        ``disk_free_gb``, ``disk_total_gb`` of the data folder's disk, ``mem_available_gb`` and ``mem_total_gb``
        (Linux only; None elsewhere) and ``snapshots`` (names, newest first).
    """
    du = shutil.disk_usage(config.data_dir())
    mem_total = mem_avail = None
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        vals = {ln.split(":")[0]: int(ln.split()[1]) for ln in meminfo.read_text().splitlines() if ":" in ln}
        mem_total, mem_avail = vals.get("MemTotal", 0) / 2**20, vals.get("MemAvailable", 0) / 2**20
    return {"disk_free_gb": round(du.free / 2**30, 1), "disk_total_gb": round(du.total / 2**30, 1),
            "mem_available_gb": round(mem_avail, 1) if mem_avail is not None else None,
            "mem_total_gb": round(mem_total, 1) if mem_total is not None else None,
            "snapshots": sorted((p.name for p in backups_dir().glob("lab-*.duckdb")), reverse=True)}
