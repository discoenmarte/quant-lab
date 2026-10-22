"""The Telegram bot: read the lab from a phone, reduce risk from a phone, and notice when the lab goes quiet.

Design (decision 0012):

- **One backend, two front doors.** The bot is a separate process that talks only to the Lab API on the same machine
  (DuckDB lets one process hold the database, and that is ``qlab serve``). It never opens the database itself.
- **Long polling, no open port.** It asks Telegram for new messages (``getUpdates``) and answers them, so the server
  needs no inbound connection, domain or certificate.
- **Only the owner's chat.** Messages from any chat but ``QLAB_TELEGRAM_CHAT`` are ignored and never answered.
- **Asymmetric, like the web app.** Reading is free (``/status``, ``/pnl``, ``/report``, ``/board``, ``/alerts``,
  ``/mining``). Reducing risk works from the phone: ``/pause`` at once, ``/kill`` after typing back a code that
  expires in two minutes. Nothing that adds risk exists here: no resume, no enrolment, no looser limits.
- **A watchdog inside, and one outside.** Every few minutes the bot checks that the API answers and that a daily
  report arrived after the last US session; either failing sends one message straight to Telegram (it doesn't need
  the database). A dead server can't report itself, so the jobs also ping an outside dead-man's switch
  (healthchecks.io, see ``ops/server``) when the owner sets one up.
- **Secrets stay in the process.** The token is never logged or put in an error message (httpx errors carry the URL,
  which contains it, so only the error's type is reported).
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from . import config
from .config import EXCHANGE_TZ
from .timeutil import now_utc

TELEGRAM_API = "https://api.telegram.org"
DEFAULT_API = "http://127.0.0.1:8765/api"
KILL_SECONDS = 120
API_DOWN_MINUTES = 10
REPORT_GRACE_HOURS = 10  # the nightly job starts at 18:00 New York and may wait up to five hours for Yahoo
WATCH_EVERY = 300.0
HELP = """quant-lab bot (paper trading only)

Read:
/status  trading mode, sleeves and the latest report
/pnl     each sleeve and each paper-book entry
/report  the latest daily report
/board   strategies by lifecycle state
/alerts  the last alerts
/mining  recent mining jobs and budgets

Reduce risk:
/pause   stop new orders (positions kept)
/kill    stop everything and sell every sleeve (asks for a code)

Nothing here can add risk: resuming, enrolling and loosening limits are done on the web app, with a reason and a
24-hour wait."""


class TelegramError(RuntimeError):
    """Telegram couldn't be reached or refused a request. The message never holds the token."""


class Telegram:
    """The Bot API for one bot and one chat.

    Args:
        token: Bot token from @BotFather.
        chat: The owner's chat id.
        client: HTTP client (replaced in tests).
    """

    def __init__(self, token: str, chat: str, client: httpx.Client | None = None) -> None:
        self._token, self.chat = token, str(chat)
        self._client = client or httpx.Client(timeout=70)

    def _call(self, method: str, **params: Any) -> Any:
        """Call one Bot API method.

        Args:
            method: Method name, e.g. ``getUpdates``.
            **params: JSON body.

        Returns:
            The ``result`` field.

        Raises:
            TelegramError: The request failed or Telegram answered ``ok: false``.
        """
        try:
            res = self._client.post(f"{TELEGRAM_API}/bot{self._token}/{method}", json=params)
        except httpx.HTTPError as err:
            raise TelegramError(f"telegram {method} failed ({type(err).__name__})") from None
        try:
            body = res.json()
        except ValueError:
            body = {}
        if res.status_code != 200 or not body.get("ok"):
            text = f"telegram {method} refused: HTTP {res.status_code} {str(body.get('description', ''))[:200]}"
            raise TelegramError(text.replace(self._token, "***"))
        return body.get("result")

    def updates(self, offset: int, timeout: int = 50) -> list[dict[str, Any]]:
        """New messages since ``offset`` (waits up to ``timeout`` seconds for one).

        Args:
            offset: The id after the last update handled.
            timeout: Long-poll seconds.

        Returns:
            Update objects.
        """
        return self._call("getUpdates", offset=offset, timeout=timeout, allowed_updates=["message"]) or []

    def send(self, text: str) -> None:
        """Send a message to the owner's chat.

        Args:
            text: Plain text (cut to 4,000 characters).
        """
        self._call("sendMessage", chat_id=self.chat, text=text[:4000], disable_web_page_preview=True)

    def chats(self) -> list[dict[str, Any]]:
        """Chats that have written to the bot recently, for finding the owner's chat id once.

        Returns:
            ``id``, ``type`` and ``name`` per chat, from the pending updates (nothing is marked as read).
        """
        seen: dict[str, dict[str, Any]] = {}
        for u in self._call("getUpdates", timeout=0) or []:
            chat = (u.get("message") or {}).get("chat") or {}
            if "id" in chat:
                name = chat.get("username") or chat.get("title") or chat.get("first_name") or ""
                seen[str(chat["id"])] = {"id": str(chat["id"]), "type": chat.get("type", ""), "name": name}
        return list(seen.values())


class Lab:
    """The Lab API, as the bot sees it.

    Args:
        api: Base URL.
        client: HTTP client (replaced in tests).
    """

    def __init__(self, api: str = DEFAULT_API, client: Any = None) -> None:
        self.client = client or httpx.Client(base_url=api, timeout=60)

    def get(self, path: str, **params: Any) -> Any:
        """GET a path and decode it.

        Args:
            path: Path under the API, e.g. ``/broker/status``.
            **params: Query parameters.

        Returns:
            The JSON body.

        Raises:
            httpx.HTTPError: The API couldn't be reached or answered with an error.
        """
        res = self.client.get(path, params=params or None)
        res.raise_for_status()
        return res.json()

    def post(self, path: str, body: dict[str, Any]) -> Any:
        """POST a JSON body and decode the answer.

        Args:
            path: Path under the API.
            body: JSON body.

        Returns:
            The JSON body of the answer.

        Raises:
            httpx.HTTPError: The API couldn't be reached or answered with an error.
        """
        res = self.client.post(path, json=body)
        res.raise_for_status()
        return res.json()


@dataclass
class State:
    """What the bot remembers between messages.

    Attributes:
        kill_code: The code ``/kill`` asked for, if any.
        kill_until: When that code expires.
        api_down_since: First failed API check of the current outage.
        warned: Watchdog conditions already reported (so each is sent once).
    """

    kill_code: str = ""
    kill_until: datetime | None = None
    api_down_since: datetime | None = None
    warned: set[str] = field(default_factory=set)


def _money(x: Any) -> str:
    """A number with thousands separators, or a dash.

    Args:
        x: Number or None.

    Returns:
        Text.
    """
    return f"{x:,.2f}" if isinstance(x, int | float) else "–"


def _pct(x: Any) -> str:
    """A fraction as a signed percentage, or a dash.

    Args:
        x: Fraction or None.

    Returns:
        Text.
    """
    return f"{x * 100:+.2f}%" if isinstance(x, int | float) else "–"


def _status(lab: Lab) -> str:
    """The ``/status`` answer.

    Args:
        lab: The Lab API.

    Returns:
        Trading mode, broker accounts, sleeves and the first lines of the latest report.
    """
    st = lab.get("/broker/status")
    ctl = st["controls"]
    lines = [f"Trading: {ctl['mode'].upper()}" + (f" since {str(ctl['since'])[:16]} ({ctl['reason']})"
                                                   if ctl["mode"] != "active" else "") + " — paper only"]
    for name, b in st["brokers"].items():
        if b.get("account"):
            rec = b.get("reconciliation") or {}
            lines.append(f"{name}: equity {_money(b['account']['equity'])} {b['account']['currency']}, "
                         + ("reconciled" if rec.get("ok", True) else "MISMATCH"))
        elif not b.get("configured"):
            lines.append(f"{name}: not configured")
        else:
            lines.append(f"{name}: {b.get('error', 'no answer')[:80]}")
    for sl in st["sleeves"]:
        lines.append(f"sleeve {sl['entry_id']}: {_money(sl['equity'])} ({_pct(sl['return'])}), "
                     f"{len(sl['holdings'])} holdings, {sl['state']}")
    rep = lab.get("/report/latest")
    if rep:
        lines += ["", f"Report {rep['created_at'][:16]} UTC:"] + [f"• {h['text']}" for h in rep["headline"][:5]]
    return "\n".join(lines)


def _pnl(lab: Lab) -> str:
    """The ``/pnl`` answer.

    Args:
        lab: The Lab API.

    Returns:
        Each sleeve's equity and return, then each active paper-book entry against its benchmark.
    """
    st = lab.get("/broker/status")
    lines = [f"Sleeve {sl['entry_id']} ({sl['broker']}): {_money(sl['equity'])} of {_money(sl['capital'])}, "
             f"{_pct(sl['return'])}" for sl in st["sleeves"]] or ["No broker sleeve yet."]
    for e in lab.get("/paper/book")["entries"]:
        if e["status"] != "active":
            continue
        band = (e.get("band") or {}).get("status")
        lines.append(f"{e['question']} ({e['verdict']}): {_pct(e['forward_return'])} over {e['n_days']} days vs "
                     f"{_pct(e['benchmark_return'])} for its benchmark"
                     + (f", {band} its band" if band else "")
                     + (", DECAY ALARM" if (e.get("alarm") or {}).get("alarm") else ""))
    return "\n".join(lines)


def _report(lab: Lab) -> str:
    """The ``/report`` answer.

    Args:
        lab: The Lab API.

    Returns:
        The latest daily report's headline.
    """
    rep = lab.get("/report/latest")
    if not rep:
        return "No daily report yet."
    return "\n".join([f"Report {rep['report_id']}, {rep['created_at'][:16]} UTC"]
                     + [f"• {h['text']}" for h in rep["headline"]])


def _board(lab: Lab) -> str:
    """The ``/board`` answer.

    Args:
        lab: The Lab API.

    Returns:
        Cards per lifecycle state, then the cards that aren't rejected.
    """
    reg = lab.get("/registry")
    labels = {s["key"]: s["label"] for s in reg["states"]}
    lines = [", ".join(f"{labels.get(k, k)} {v}" for k, v in reg["counts"].items())]
    for c in reg["cards"]:
        if c["state"] not in ("rejected", "retired"):
            lines.append(f"• {labels.get(c['state'], c['state'])}: {c['question']} ({c['verdict']})")
    return "\n".join(lines)


def _alerts(lab: Lab) -> str:
    """The ``/alerts`` answer.

    Args:
        lab: The Lab API.

    Returns:
        The five latest alerts.
    """
    rows = lab.get("/alerts", limit=5)
    return "\n".join(f"{r['ts'][:16]} [{r['severity']}] {r['title']}" for r in rows) or "No alerts yet."


def _mining(lab: Lab) -> str:
    """The ``/mining`` answer.

    Args:
        lab: The Lab API.

    Returns:
        The five latest jobs and every mining budget.
    """
    lines = [f"{j['created_at'][:10]} {j['question']} {j['paradigm']} {j['status']}, {j['n_trials'] or 0} trials, "
             f"best {j['best_edge']:+.2f}" if j.get("best_edge") is not None else
             f"{j['created_at'][:10]} {j['question']} {j['paradigm']} {j['status']}"
             for j in lab.get("/mining/jobs", limit=5)]
    lines += [f"budget {b['question']}: {b['used']} of {b['max_trials']} used" for b in lab.get("/mining/budgets")]
    return "\n".join(lines) or "No mining yet."


READERS: dict[str, Callable[[Lab], str]] = {"/status": _status, "/pnl": _pnl, "/report": _report, "/board": _board,
                                            "/alerts": _alerts, "/mining": _mining}


def handle(text: str, lab: Lab, state: State, now: datetime | None = None) -> str:
    """Answer one message from the owner.

    Args:
        text: The message.
        lab: The Lab API.
        state: The bot's memory (the pending kill code).
        now: Time (tz-aware); None means now.

    Returns:
        The reply.
    """
    now = now or now_utc()
    words = text.strip().split()
    if not words:
        return HELP
    cmd = words[0].split("@")[0].lower()
    try:
        if cmd in READERS:
            return READERS[cmd](lab)
        if cmd == "/pause":
            scope = words[1] if len(words) > 1 else "all"
            lab.post("/broker/pause", {"scope": scope, "reason": "paused from Telegram"})
            return (f"Paused {scope}: no new orders; positions are kept. Resuming needs the web app, a reason and "
                    "24 hours.")
        if cmd == "/kill":
            if len(words) == 1:
                state.kill_code = f"{secrets.randbelow(10_000):04d}"
                state.kill_until = now + timedelta(seconds=KILL_SECONDS)
                return (f"This stops all paper trading, cancels open orders and sells every sleeve. To confirm, send "
                        f"/kill {state.kill_code} within {KILL_SECONDS // 60} minutes.")
            if state.kill_code and state.kill_until and now <= state.kill_until and words[1] == state.kill_code:
                state.kill_code, state.kill_until = "", None
                out = lab.post("/broker/kill", {"reason": "kill switch from Telegram"})
                n = sum(len(v) for v in (out.get("flatten") or {}).values())
                return (f"KILLED. {len(out.get('cancelled') or [])} open orders cancelled, {n} sell orders sent. "
                        "Lifting it needs the web app, a reason and 24 hours.")
            state.kill_code, state.kill_until = "", None
            return "That code is wrong or expired; nothing was done. Send /kill again for a new code."
        return HELP
    except httpx.HTTPError as err:
        return f"The lab didn't answer ({type(err).__name__}). Is qlab serve running?"
    except (KeyError, TypeError, ValueError) as err:
        return f"Couldn't read the lab's answer ({type(err).__name__}: {err})."


def last_session_close(now: datetime) -> datetime:
    """The latest weekday 16:00 New York close before a moment.

    Args:
        now: Tz-aware time.

    Returns:
        That close, tz-aware.
    """
    ny = now.astimezone(ZoneInfo(EXCHANGE_TZ))
    day = ny.date()
    while True:
        close = datetime(day.year, day.month, day.day, 16, tzinfo=ZoneInfo(EXCHANGE_TZ))
        if close.weekday() < 5 and close <= ny:
            return close
        day -= timedelta(days=1)


def watch(lab: Lab, state: State, now: datetime | None = None) -> list[str]:
    """Watchdog checks: the API answers, and a daily report followed the last US close in good time.

    Each condition is reported once while it lasts and again only after it clears and comes back.

    Args:
        lab: The Lab API.
        state: The bot's memory.
        now: Time (tz-aware); None means now.

    Returns:
        Messages to send now.
    """
    now = now or now_utc()
    out = []
    try:
        lab.get("/health")
        if "api_down" in state.warned:
            out.append("The Lab API answers again.")
        state.api_down_since = None
        state.warned.discard("api_down")
    except httpx.HTTPError:
        state.api_down_since = state.api_down_since or now
        if now - state.api_down_since >= timedelta(minutes=API_DOWN_MINUTES) and "api_down" not in state.warned:
            state.warned.add("api_down")
            out.append(f"The Lab API hasn't answered for {API_DOWN_MINUTES} minutes: the nightly job, the broker steps "
                       "and mining can't run. Check the qlab-api service.")
        return out
    close = last_session_close(now)
    key = f"no_report:{close.date()}"
    if now - close >= timedelta(hours=REPORT_GRACE_HOURS):
        try:
            rep = lab.get("/report/latest")
        except httpx.HTTPError:
            return out
        made = None
        if rep:
            made = datetime.fromisoformat(str(rep["created_at"]))
            made = made.replace(tzinfo=ZoneInfo("UTC")) if made.tzinfo is None else made
        if (made is None or made < close) and key not in state.warned:
            state.warned.add(key)
            out.append(f"No daily report since the {close.date()} US close: the nightly job didn't run or didn't "
                       "finish. Check the qlab-nightly timer.")
    return out


def _offset_path():
    """Where the bot keeps the id of the last update it handled.

    Returns:
        ``<data>/ops/telegram_offset``.
    """
    return config.data_dir() / "ops" / "telegram_offset"


def run(api: str = DEFAULT_API, log: Callable[[str], None] = print, once: bool = False,
        telegram: Telegram | None = None, lab: Lab | None = None) -> None:
    """Answer the owner's messages and run the watchdog, until stopped.

    Args:
        api: Lab API base URL.
        log: Receives one line per message handled (never the token or the message text of strangers).
        once: Handle one round of updates and one watchdog check, then return (for tests).
        telegram: Bot API client; None builds one from ``QLAB_TELEGRAM_TOKEN`` and ``QLAB_TELEGRAM_CHAT``.
        lab: Lab API client; None builds one on ``api``.

    Raises:
        RuntimeError: The token or chat id isn't set.
    """
    if telegram is None:
        token, chat = config.setting("QLAB_TELEGRAM_TOKEN"), config.setting("QLAB_TELEGRAM_CHAT")
        if not token or not chat:
            raise RuntimeError(f"set QLAB_TELEGRAM_TOKEN and QLAB_TELEGRAM_CHAT in {config.env_file()}")
        telegram = Telegram(token, chat)
    lab = lab or Lab(api)
    state, path = State(), _offset_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    offset = int(path.read_text().strip() or 0) if path.exists() else 0
    next_watch = 0.0
    while True:
        try:
            for u in telegram.updates(offset, timeout=0 if once else 50):
                offset = max(offset, int(u["update_id"]) + 1)
                path.write_text(str(offset))
                msg = u.get("message") or {}
                if str((msg.get("chat") or {}).get("id")) != telegram.chat:
                    log("ignored a message from another chat")
                    continue
                reply = handle(msg.get("text") or "", lab, state)
                telegram.send(reply)
                log(f"answered {(msg.get('text') or '').split()[:1]}")
            if time.monotonic() >= next_watch:
                next_watch = time.monotonic() + WATCH_EVERY
                for text in watch(lab, state):
                    telegram.send(f"⚠️ {text}")
                    log(f"watchdog: {text[:60]}")
        except TelegramError as err:
            log(str(err))
            if not once:
                time.sleep(10)
        if once:
            return


def describe_chats(telegram: Telegram) -> str:
    """Plain-language list of chats that wrote to the bot, for setting ``QLAB_TELEGRAM_CHAT``.

    Args:
        telegram: Bot API client (its chat may be empty).

    Returns:
        One line per chat, or what to do when none has written yet.
    """
    rows = telegram.chats()
    if not rows:
        return "Nobody has written to the bot yet: open it in Telegram, press Start or send any message, then retry."
    return "\n".join(f"chat id {r['id']}  ({r['type']} {r['name']})" for r in rows) + "\n" + json.dumps(
        {"QLAB_TELEGRAM_CHAT": rows[0]["id"]})
