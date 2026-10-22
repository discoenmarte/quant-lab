"""Alerts: four rules that turn the daily report into messages, and the channels that deliver them.

Design (decision 0008):

- **Rules fire on changes, not on states.** A condition (a card with a candidate verdict, a decay alarm on an entry, a
  stale source) raises an alert in the first report it appears in, then stays quiet while it lasts; if it clears and
  comes back, it alerts again. Each report stores the conditions it saw. A failed nightly job alerts every time it
  fails, since each failure is a new event.
- **The rules:** a new candidate (a card passed stages 1-9) or a newly validated card (it passed its lockbox); a
  paper-book decay alarm (the CUSUM, tuned to one false alarm in ten years); stale data (beyond the tolerances in
  ``data.freshness``); a failed nightly job. A forward edge outside its 90% band is not an alert: by design it happens
  one day in ten while the edge is exactly as researched. The report shows it.
- **Channels:** ``file`` (JSON lines in ``<data>/alerts/alerts.jsonl``, always on), ``macos`` (a notification through
  ``osascript``; on by default on macOS) and ``telegram`` (only when ``QLAB_TELEGRAM_TOKEN`` and ``QLAB_TELEGRAM_CHAT``
  are set, in the environment or in ``<data>/lab.env``). ``QLAB_ALERT_CHANNELS`` (comma-separated) replaces the list.
- **Secrets stay in the process.** The token is never logged, stored, returned by the API or put in an error message.
- **Delivery never raises.** A failing channel is recorded on the alert and the others still deliver, so a broken
  Telegram setup can't stop the nightly job.
- Alerts only inform. Nothing here can change positions, the book or any limit.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx

from . import audit, config, db
from .timeutil import naive_utc

CHANNELS = ("file", "macos", "telegram")
TELEGRAM_API = "https://api.telegram.org"
ICONS = {"info": "ℹ️", "good": "✅", "warn": "⚠️", "bad": "🛑"}


@dataclass
class Alert:
    """One message for the owner.

    Attributes:
        rule: ``new_candidate``, ``paper_alarm``, ``stale_data``, ``nightly_failed``, ``daily_report``, ``radar`` (the
            radar's anomalies, raised by ``radar.scan``) or ``test``.
        severity: ``info``, ``good``, ``warn`` or ``bad``.
        key: Identity of the condition, used to alert once per occurrence.
        title: One line.
        message: A few sentences with the numbers.
        link: Page of the web app that shows the details.
    """

    rule: str
    severity: str
    key: str
    title: str
    message: str
    link: str = "/board"


class Channel(Protocol):
    """Something that can deliver an alert. ``send`` raises on failure; ``deliver`` catches it."""

    name: str

    def send(self, alert: Alert) -> None:
        """Deliver one alert.

        Args:
            alert: The alert.
        """


class FileChannel:
    """Appends each alert as one JSON line to ``<data>/alerts/alerts.jsonl``: the local record, for ``tail -f``."""

    name = "file"

    def send(self, alert: Alert) -> None:
        """Append the alert with its time.

        Args:
            alert: The alert.
        """
        path = config.data_dir() / "alerts" / "alerts.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps({"ts": naive_utc().isoformat(timespec="seconds"), **asdict(alert)}) + "\n")


class MacOSChannel:
    """Shows a macOS notification through ``osascript``. Text is passed as arguments, never pasted into the script."""

    name = "macos"
    SCRIPT = ["on run argv", "display notification (item 2 of argv) with title (item 1 of argv)", "end run"]

    def send(self, alert: Alert) -> None:
        """Show the notification.

        Args:
            alert: The alert.

        Raises:
            RuntimeError: ``osascript`` is missing or failed.
        """
        exe = shutil.which("osascript")
        if exe is None:
            raise RuntimeError("osascript not found (macOS only)")
        script = [a for line in self.SCRIPT for a in ("-e", line)]
        args = [exe, *script, f"quant-lab: {alert.title}", alert.message[:240]]
        res = subprocess.run(args, capture_output=True, text=True, timeout=15, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"osascript exited {res.returncode}: {res.stderr.strip()[:200]}")


class TelegramChannel:
    """Sends the alert to one Telegram chat through the Bot API."""

    name = "telegram"

    def __init__(self, token: str, chat: str) -> None:
        """Keep the bot token private to the object.

        Args:
            token: Bot token from @BotFather.
            chat: Chat id to send to.
        """
        self._token = token
        self.chat = chat

    def _clean(self, text: str) -> str:
        """Remove the token from a piece of text.

        Args:
            text: Any text that might contain it.

        Returns:
            The text with the token replaced by ``***``.
        """
        return text.replace(self._token, "***") if self._token else text

    def send(self, alert: Alert) -> None:
        """Post the message.

        Args:
            alert: The alert.

        Raises:
            RuntimeError: The request failed or Telegram refused it. The message never contains the token.
        """
        text = f"{ICONS.get(alert.severity, '')} {alert.title}\n\n{alert.message}".strip()
        try:
            res = httpx.post(f"{TELEGRAM_API}/bot{self._token}/sendMessage", timeout=15,
                             json={"chat_id": self.chat, "text": text[:4000], "disable_web_page_preview": True})
        except httpx.HTTPError as err:
            # httpx errors carry the request URL, which contains the token; report the type only.
            raise RuntimeError(f"telegram request failed ({type(err).__name__})") from None
        if res.status_code != 200:
            raise RuntimeError(self._clean(f"telegram refused the message: HTTP {res.status_code} {res.text[:200]}"))


def channel_names() -> list[str]:
    """Names of the channels in use.

    Returns:
        ``QLAB_ALERT_CHANNELS`` when set; otherwise ``file``, plus ``macos`` on macOS, plus ``telegram`` when its token
        and chat id are both set.
    """
    raw = config.setting("QLAB_ALERT_CHANNELS")
    if raw is not None:
        return [n.strip() for n in raw.split(",") if n.strip() in CHANNELS]
    names = ["file"]
    if sys.platform == "darwin":
        names.append("macos")
    if config.setting("QLAB_TELEGRAM_TOKEN") and config.setting("QLAB_TELEGRAM_CHAT"):
        names.append("telegram")
    return names


def channels(notify: bool = True) -> list[Channel]:
    """The channels to deliver through.

    Args:
        notify: False keeps only the file channel (quiet runs still leave a record).

    Returns:
        Channel objects. Telegram is left out when its token or chat id is missing, even if listed.
    """
    out: list[Channel] = []
    for name in channel_names():
        if name == "file":
            out.append(FileChannel())
        elif notify and name == "macos":
            out.append(MacOSChannel())
        elif notify and name == "telegram":
            token, chat = config.setting("QLAB_TELEGRAM_TOKEN"), config.setting("QLAB_TELEGRAM_CHAT")
            if token and chat:
                out.append(TelegramChannel(token, chat))
    if not any(c.name == "file" for c in out):
        out.insert(0, FileChannel())  # the local record is never switched off
    return out


def channel_status() -> list[dict[str, Any]]:
    """What each channel would do, without revealing any secret.

    Returns:
        One row per channel: ``name``, ``enabled`` and ``detail`` in plain words.
    """
    names = channel_names()
    token, chat = config.setting("QLAB_TELEGRAM_TOKEN"), config.setting("QLAB_TELEGRAM_CHAT")
    tg = ("token and chat id set" + (f" (chat …{chat[-3:]})" if chat else "")) if token and chat else (
        "set QLAB_TELEGRAM_TOKEN and QLAB_TELEGRAM_CHAT in the environment or in " + str(config.env_file()))
    return [
        {"name": "file", "enabled": True, "detail": str(config.data_dir() / "alerts" / "alerts.jsonl")},
        {"name": "macos", "enabled": "macos" in names and shutil.which("osascript") is not None,
         "detail": "Notification Center" if sys.platform == "darwin" else "macOS only"},
        {"name": "telegram", "enabled": "telegram" in names and bool(token and chat), "detail": tg},
    ]


def _num(x: Any, fmt: str = "{:.2f}") -> str:
    """Format a number for a message, with a dash when missing.

    Args:
        x: Value.
        fmt: Format string.

    Returns:
        Text.
    """
    return "–" if x is None else fmt.format(x)


def conditions(report: dict[str, Any]) -> dict[str, Alert]:
    """The alert-worthy conditions a report shows, each as the alert it would raise.

    Args:
        report: A daily report (``report.build``), with ``registry.cards``, ``paper.entries`` and ``freshness``.

    Returns:
        Condition key mapped to its alert.
    """
    out: dict[str, Alert] = {}
    for c in report["registry"]["cards"]:
        nums = (f"edge Sharpe {_num(c.get('edge'))}, permutation p {_num(c.get('perm_p'), '{:.3f}')}, "
                f"DSR {_num(c.get('dsr'), '{:.2f}')}")
        if c["verdict"] == "candidate":
            key = f"candidate:{c['card_id']}"
            out[key] = Alert("new_candidate", "good", key, f"New candidate: {c['question']}",
                             f"Card {c['card_id']} passed gauntlet stages 1-9 ({nums}). Its lockbox is still sealed. "
                             "Next steps are yours: open the lockbox once, or put the card in the paper book.",
                             "/board")
        elif c["verdict"] == "validated":
            key = f"validated:{c['card_id']}"
            out[key] = Alert("new_candidate", "good", key, f"Validated: {c['question']}",
                             f"Card {c['card_id']} passed every stage including its lockbox ({nums}). It still needs "
                             "months of forward paper evidence before the go-live gate.", "/board")
    for e in report["paper"]["entries"]:
        alarm = e.get("alarm") or {}
        if e["status"] == "active" and alarm.get("alarm"):
            key = f"alarm:{e['entry_id']}"
            value, h = _num(alarm.get("value"), "{:.1f}"), _num(alarm.get("h"), "{:.1f}")
            out[key] = Alert("paper_alarm", "bad", key, f"Decay alarm: {e['question']}",
                             f"The CUSUM decay alarm of paper entry {e['entry_id']} is at {value}, above its threshold "
                             f"{h} (first crossed "
                             f"{alarm.get('first_alarm') or '–'}). The forward edge looks lower than researched; at "
                             "this threshold a false alarm comes about once every ten years.", "/desk")
    for f in report["freshness"]:
        if f["stale"]:
            key = f"stale:{f['source']}:{f['interval']}"
            out[key] = Alert("stale_data", "warn", key, f"Stale data: {f['source']} {f['interval']}",
                             f"{f['message']} Decisions in the paper book are refused while data is stale; run an "
                             "ingest (Data Health page or qlab ingest).", "/data")
    return out


def nightly_alert(nightly: dict[str, Any], key: str) -> Alert:
    """The alert for a failed nightly job, saying which step failed.

    Args:
        nightly: The job's summary (``paper.nightly.summary``).
        key: Unique key for this failure.

    Returns:
        A ``nightly_failed`` alert.
    """
    reasons = []
    if nightly.get("error"):
        reasons.append(f"the job stopped: {nightly['error']}")
    for r in nightly.get("ingest", []):
        if r.get("status") == "error":
            reasons.append(f"ingest {r['source']} failed ({'; '.join(r.get('errors', [])[:1]) or 'no detail'})")
    run = nightly.get("run") or {}
    reasons += [f"decision: {m}" for m in run.get("messages", [])[:3]]
    if nightly.get("report_error"):
        reasons.append(f"the report failed: {nightly['report_error']}")
    text = "; ".join(reasons) or "a step reported a problem"
    return Alert("nightly_failed", "bad", key, "Nightly job failed",
                 f"The nightly job ({nightly.get('mode', '?')} mode) finished with problems: {text}. A missed decision "
                 "can't be made up later; fix it before the next close.", "/desk")


def evaluate(report: dict[str, Any], previous: set[str] | None, nightly: dict[str, Any] | None) -> list[Alert]:
    """The alerts a new report raises.

    Args:
        report: The new report.
        previous: Condition keys of the previous report; None when there is none (everything present alerts).
        nightly: Summary of the nightly job that built this report, or None.

    Returns:
        New conditions as alerts, then the nightly failure if the job failed.
    """
    seen = previous or set()
    out = [a for k, a in conditions(report).items() if k not in seen]
    if nightly is not None and not nightly.get("ok", True):
        out.append(nightly_alert(nightly, f"nightly:{report['report_id']}"))
    return out


def deliver(items: list[Alert], notify: bool = True) -> list[dict[str, str]]:
    """Send alerts through every channel. Never raises.

    Args:
        items: Alerts to send.
        notify: False sends to the file channel only.

    Returns:
        One dict per alert: channel name mapped to ``"ok"`` or ``"error: ..."``.
    """
    chans = channels(notify)
    results = []
    for alert in items:
        res: dict[str, str] = {}
        for ch in chans:
            try:
                ch.send(alert)
                res[ch.name] = "ok"
            except Exception as err:  # one channel's failure must not stop the others
                res[ch.name] = f"error: {err}"
        results.append(res)
    return results


def record(items: list[Alert], delivery: list[dict[str, str]], report_id: str | None = None,
           now: datetime | None = None) -> list[dict[str, Any]]:
    """Store alerts in the alert log.

    Args:
        items: Alerts.
        delivery: Output of ``deliver`` for the same alerts.
        report_id: Report that raised them; None for test alerts.
        now: Time to record; None means now.

    Returns:
        The stored rows, as ``recent`` returns them.
    """
    when = naive_utc(now)
    rows = [{"alert_id": uuid.uuid4().hex[:12], "created_at": when.isoformat(timespec="seconds"),
             "report_id": report_id, **asdict(a), "delivery": d} for a, d in zip(items, delivery, strict=True)]
    with db.connect() as con:
        for r in rows:
            con.execute("""INSERT INTO alerts (alert_id, created_at, report_id, rule, severity, key, title, message,
                                               link, delivery) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        [r["alert_id"], when, report_id, r["rule"], r["severity"], r["key"], r["title"], r["message"],
                         r["link"], db.dumps(r["delivery"])])
    return rows


def recent(limit: int = 100) -> list[dict[str, Any]]:
    """The alert log, newest first.

    Args:
        limit: Maximum number of alerts.

    Returns:
        Rows ``alert_id, created_at, report_id, rule, severity, key, title, message, link`` and ``delivery`` (dict).
    """
    rows = db.rows("SELECT * FROM alerts ORDER BY created_at DESC, alert_id LIMIT ?", [limit])
    for r in rows:
        r["delivery"] = json.loads(r["delivery"] or "{}")
    return rows


def send_test(actor: str = "user") -> dict[str, Any]:
    """Send a test alert through every channel, so the owner can check each one works.

    Args:
        actor: Who asked, for the audit log.

    Returns:
        The stored alert row, with ``delivery`` per channel.
    """
    alert = Alert("test", "info", f"test:{uuid.uuid4().hex[:8]}", "Test alert",
                  "If you can read this, alerts from the lab reach you here.", "/board")
    delivery = deliver([alert])
    row = record([alert], delivery)[0]
    audit.log("alerts.test", target=row["alert_id"], actor=actor, details=delivery[0])
    return row
