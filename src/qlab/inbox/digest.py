"""The inbox's routine run and its digest: fetch, triage, draft specs for the best papers, and summarise.

Design (decision 0010):

- ``run`` is one pass of the inbox: new papers from arXiv, the rubric on each, and a draft spec for the few that
  score highest. It is meant for a weekly (or nightly) schedule the owner chooses; with a local 35B model a pass of
  100 abstracts takes several minutes.
- ``digest`` summarises a period: papers seen, the best of them with their rubric dots, specs waiting for review,
  replications and gauntlet verdicts, and what the model calls cost. It is saved under ``reports/inbox`` and its
  headline is delivered through the alert channels (decision 0008), so the owner hears about new candidates
  without opening the lab.
- Nothing here approves, replicates or judges: those stay the owner's steps.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from .. import alerts, audit, config, db
from ..timeutil import naive_utc, now_utc
from . import feed, spec, triage


def run(max_papers: int = 100, triage_limit: int = 100, spec_min_score: int = 5, spec_limit: int = 3,
        actor: str = "user", log=None) -> dict[str, Any]:
    """One pass of the inbox: fetch, triage, draft specs for the best new papers, then the digest.

    Each step runs even if an earlier one failed (a dead arXiv doesn't stop triage of what is stored).

    Args:
        max_papers: Most papers to fetch.
        triage_limit: Most papers to triage in this pass.
        spec_min_score: Rubric score a paper needs for a draft spec.
        spec_limit: Most specs drafted in this pass.
        actor: Who ran it.
        log: Receives progress lines.

    Returns:
        ``fetch`` (counts or error), ``triage`` (rows or error), ``specs`` (rows) and ``digest`` (the saved digest).
    """
    say = log or (lambda _msg: None)
    out: dict[str, Any] = {}
    try:
        out["fetch"] = feed.fetch_and_store(max_papers, actor=actor, log=say)
    except feed.FeedError as err:
        out["fetch"] = {"error": str(err)}
        say(f"fetch failed: {err}")
    try:
        out["triage"] = triage.triage(triage_limit, actor=actor, log=say)
        out["specs"] = spec.draft_top(spec_min_score, spec_limit, actor=actor, log=say)
    except Exception as err:  # the model may be down; the digest still reports what is stored
        out["triage"], out["specs"] = {"error": str(err)}, []
        say(f"triage failed: {err}")
    out["digest"] = digest(actor=actor)
    return out


def digest(days: int = 7, now: datetime | None = None, notify: bool = True, actor: str = "user") -> dict[str, Any]:
    """Summarise the inbox over the last ``days`` and save it.

    Args:
        days: Length of the period.
        now: End of the period (tz-aware); None means now.
        notify: Send the headline through the alert channels.
        actor: Who asked.

    Returns:
        ``digest_id, created_at, since, headline`` (lines), ``top`` (the best papers seen in the period),
        ``waiting`` (specs drafted and not reviewed), ``replications``, ``judged``, ``usage`` (calls, seconds,
        tokens and cost of the period) and ``path``.
    """
    now = now or now_utc()
    since = naive_utc(now - timedelta(days=days))
    seen = int(db.scalar("SELECT count(*) FROM papers WHERE fetched_at >= ?", [since]) or 0)
    top = [p for p in triage.latest(1, 400)
           if p["triaged_at"] and datetime.fromisoformat(str(p["triaged_at"])) >= since][:10]
    waiting = [s for s in spec.specs() if s["status"] in ("draft", "needs_code")]
    replications = [s for s in spec.specs() if s["results"] and s["status"] in ("replicated", "not_replicated")]
    judged = [s for s in spec.specs("judged")]
    usage = db.rows("""SELECT count(*) AS calls, coalesce(sum(seconds), 0) AS seconds,
                              coalesce(sum(tokens_in + tokens_out), 0) AS tokens, coalesce(sum(cost_usd), 0) AS cost
                       FROM llm_calls WHERE ts >= ?""", [since])[0]
    headline = [f"{seen} new papers in the last {days} days; {len(top)} scored on the rubric at 1/7 or more."]
    if top:
        best = top[0]
        headline.append(f"Best: {best['title'][:90]} ({best['score']}/7): {best['one_line'] or ''}"[:220])
    headline.append(f"{len(waiting)} specs wait for your review" + (
        f"; {sum(s['status'] == 'needs_code' for s in waiting)} need a new template." if waiting else "."))
    if replications:
        ok = sum(s["status"] == "replicated" for s in replications)
        headline.append(f"{ok} of {len(replications)} replications matched the paper's claim.")
    headline.append(f"Model use: {usage['calls']} calls, {usage['seconds'] / 60:.0f} minutes, "
                    f"{usage['cost']:.2f} USD.")
    rep = {"digest_id": uuid.uuid4().hex[:10], "created_at": naive_utc(now).isoformat(timespec="seconds"),
           "since": since.isoformat(timespec="seconds"), "headline": headline,
           "top": [{k: p[k] for k in ("arxiv_id", "title", "score", "dots", "one_line", "url", "asset_class")}
                   for p in top],
           "waiting": [{"spec_id": s["spec_id"], "title": s["title"], "status": s["status"],
                        "template": s["spec"].get("template"), "target": s["spec"].get("target")} for s in waiting],
           "replications": [{"spec_id": s["spec_id"], "title": s["title"], "status": s["status"]}
                            for s in replications],
           "judged": [{"spec_id": s["spec_id"], "card_id": s["card_id"]} for s in judged], "usage": usage}
    path = config.reports_dir() / "inbox" / f"{rep['digest_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rep["path"] = str(path)
    path.write_text(json.dumps(db.json_safe(rep), default=str))
    if notify:
        item = alerts.Alert(rule="inbox_digest", severity="info", key=f"inbox:{rep['digest_id']}",
                            title="Research inbox digest", message="\n".join(headline), link="/inbox")
        alerts.record([item], alerts.deliver([item]))
    audit.log("inbox.digest", target=rep["digest_id"], actor=actor, details={"papers": seen, "waiting": len(waiting)})
    return rep


def latest_digest() -> dict[str, Any] | None:
    """The most recent saved digest.

    Returns:
        The digest, or None when there is none.
    """
    folder = config.reports_dir() / "inbox"
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime) if folder.exists() else []
    return json.loads(files[-1].read_text()) if files else None


def usage(days: int = 30) -> dict[str, Any]:
    """The model cost meter: calls, time, tokens and cost per provider over the last ``days``, and this month's
    spend against the budget.

    Args:
        days: Length of the window.

    Returns:
        ``by_provider`` rows and ``month`` (``cost_usd``, ``budget_usd``).
    """
    since = naive_utc(now_utc() - timedelta(days=days))
    rows = db.rows("""SELECT provider, model, purpose, count(*) AS calls, sum(CASE WHEN ok THEN 1 ELSE 0 END) AS ok,
                             coalesce(sum(seconds), 0) AS seconds, coalesce(sum(tokens_in), 0) AS tokens_in,
                             coalesce(sum(tokens_out), 0) AS tokens_out, coalesce(sum(cost_usd), 0) AS cost_usd
                      FROM llm_calls WHERE ts >= ? GROUP BY ALL ORDER BY calls DESC""", [since])
    return {"by_provider": rows, "month": {"cost_usd": triage.month_cost(),
                                           "budget_usd": float(config.setting("QLAB_LLM_BUDGET_USD") or 0)}}
