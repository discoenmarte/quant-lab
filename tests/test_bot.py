"""Tests for the Telegram bot: reading through the Lab API, the two-step kill, strangers and the watchdog."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from qlab import bot, config
from qlab.api.main import create_app
from qlab.broker import controls


def lab() -> bot.Lab:
    """The bot's view of a fresh lab, through the real API."""
    return bot.Lab(client=TestClient(create_app(), base_url="http://test/api"))


def test_reading_commands_answer_from_the_api():
    """Every reading command answers in plain text on an empty lab, and unknown text gets the help."""
    lb, st = lab(), bot.State()
    assert "Trading: ACTIVE" in bot.handle("/status", lb, st)
    assert "No broker sleeve yet" in bot.handle("/pnl", lb, st)
    assert bot.handle("/report", lb, st) == "No daily report yet."
    assert "Rejected 0" in bot.handle("/board", lb, st)
    assert bot.handle("/alerts", lb, st) == "No alerts yet."
    assert bot.handle("/mining", lb, st) == "No mining yet."
    assert bot.handle("hello", lb, st) == bot.HELP and bot.handle("/status@quantlab_bot", lb, st).startswith("Trading")


def test_kill_needs_the_code_typed_back_in_time_and_pause_is_instant():
    """/kill gives a code; a wrong or late code does nothing; the right one kills. /pause works at once."""
    lb, st = lab(), bot.State()
    t0 = datetime(2026, 10, 22, 15, tzinfo=UTC)
    ask = bot.handle("/kill", lb, st, now=t0)
    code = st.kill_code
    assert code in ask and len(code) == 4
    assert "wrong or expired" in bot.handle("/kill 99999", lb, st, now=t0)
    assert controls.state()["mode"] == "active"
    bot.handle("/kill", lb, st, now=t0)
    late = st.kill_code
    assert "wrong or expired" in bot.handle(f"/kill {late}", lb, st, now=t0 + timedelta(minutes=3))
    assert controls.state()["mode"] == "active"
    bot.handle("/kill", lb, st, now=t0)
    assert bot.handle(f"/kill {st.kill_code}", lb, st, now=t0 + timedelta(seconds=30)).startswith("KILLED")
    assert controls.state()["mode"] == "killed"
    lb2, st2 = lab(), bot.State()
    assert "Paused all" in bot.handle("/pause", lb2, st2)
    assert not any(w in bot.HELP.lower() for w in ("/resume", "/enroll", "/limits"))  # nothing that adds risk


def telegram(sent: list, updates: list) -> bot.Telegram:
    """A bot client on a fake Bot API that serves ``updates`` once and records every message sent."""

    def respond(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        if method == "getUpdates":
            out = [u for u in updates if u["update_id"] >= body.get("offset", 0)]
            return httpx.Response(200, json={"ok": True, "result": out})
        sent.append(body)
        return httpx.Response(200, json={"ok": True, "result": {}})

    return bot.Telegram("123:SECRET", "42", httpx.Client(transport=httpx.MockTransport(respond)))


def test_only_the_owners_chat_is_answered_and_the_offset_survives_restarts():
    """A stranger's message is ignored; the owner's gets a reply; handled updates aren't handled again."""
    sent: list = []
    ups = [{"update_id": 7, "message": {"chat": {"id": 99}, "text": "/kill"}},
           {"update_id": 8, "message": {"chat": {"id": 42}, "text": "/report"}}]
    lines: list[str] = []
    bot.run(once=True, telegram=telegram(sent, ups), lab=lab(), log=lines.append)
    replies = [m for m in sent if not m["text"].startswith("⚠️")]  # the watchdog may add its own message
    assert [m["chat_id"] for m in replies] == ["42"] and "No daily report" in replies[0]["text"]
    assert "ignored a message from another chat" in lines
    assert (config.data_dir() / "ops" / "telegram_offset").read_text() == "9"
    sent.clear()
    bot.run(once=True, telegram=telegram(sent, ups), lab=lab(), log=lambda m: None)
    assert not [m for m in sent if m["text"].startswith("No daily report")]


def test_errors_never_show_the_token():
    """A refused request is reported without the token."""

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": f"bad token {request.url}"})

    tg = bot.Telegram("123:SECRET", "42", httpx.Client(transport=httpx.MockTransport(refuse)))
    try:
        tg.send("hi")
    except bot.TelegramError as err:
        assert "SECRET" not in str(err) and "401" in str(err)
    else:
        raise AssertionError("expected a refusal")


class DownLab:
    """A Lab API that never answers."""

    def get(self, path, **params):
        raise httpx.ConnectError("refused")


def test_watchdog_reports_a_silent_api_once_and_a_missing_report():
    """An API down for ten minutes is reported once; no report eight hours after a close is reported once."""
    st = bot.State()
    t0 = datetime(2026, 10, 22, 12, tzinfo=UTC)
    assert bot.watch(DownLab(), st, t0) == []
    assert len(bot.watch(DownLab(), st, t0 + timedelta(minutes=11))) == 1
    assert bot.watch(DownLab(), st, t0 + timedelta(minutes=20)) == []
    lb = lab()
    back = bot.watch(lb, st, datetime(2026, 10, 22, 21, tzinfo=UTC))  # 17:00 New York, an hour after the close
    assert back == ["The Lab API answers again."]
    late = bot.watch(lb, st, datetime(2026, 10, 23, 7, tzinfo=UTC))  # 03:00 New York, no report yet
    assert len(late) == 1 and "2026-10-22" in late[0]
    assert bot.watch(lb, st, datetime(2026, 10, 23, 8, tzinfo=UTC)) == []
    assert bot.last_session_close(datetime(2026, 10, 25, 12, tzinfo=UTC)).date().isoformat() == "2026-10-23"


def test_saving_a_setting_replaces_its_line_and_keeps_the_file_private():
    """lab.env gets one line per name, readable by its owner only."""
    path = config.save_setting("QLAB_TELEGRAM_CHAT", "1")
    config.save_setting("OTHER", "x")
    config.save_setting("QLAB_TELEGRAM_CHAT", "2")
    assert path.read_text().splitlines() == ["OTHER=x", "QLAB_TELEGRAM_CHAT=2"]
    assert oct(path.stat().st_mode & 0o777) == "0o600" and config.setting("QLAB_TELEGRAM_CHAT") == "2"
