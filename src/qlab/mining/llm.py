"""Strategy proposals from a language model, each with an economic rationale, compiled only through the grammar.

Design:

- **Hypotheses, not optimisation.** The model is shown the grammar and the scope, never a backtest result. It must
  say why each idea should earn money, for whom it is a cost, and why it would survive trading costs. Ideas the
  grammar can't express come back as ``outside`` notes for the owner; nothing outside the grammar runs.
- **Untrusted output, validated in the loop.** The answer must parse as JSON; each proposal's spec goes through
  ``grammar.validate_spec`` and its rationale through ``check_rationale``. Rejected proposals are sent back with
  the reasons, once (Gençay, 2026: an LLM emitting JSON without in-loop validation produced 0 usable candidates of
  60). Nothing generated is executed.
- **The inbox's providers.** Ollama (``qwen3.6:35b``) locally by default, logged in the same cost meter
  (``llm_calls``, purpose ``mining``) and under the same monthly budget for paid providers.
"""

from __future__ import annotations

import json
from typing import Any

from ..inbox import llm as models
from ..inbox import triage
from . import formula, grammar
from .grammar import Space

MIN_RATIONALE = 40
MAX_RATIONALE = 1500
SCHEMA = {
    "type": "object",
    "properties": {
        "proposals": {"type": "array", "items": {
            "type": "object",
            "properties": {"rationale": {"type": "string"}, "spec": {"type": "object"}},
            "required": ["rationale", "spec"]}},
        "outside": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["proposals"],
}

SYSTEM = """You are a quantitative researcher proposing trading-strategy hypotheses for a research lab that tests
every idea against noise with strict statistics after realistic costs. You never see backtest results. Propose
ideas with a real economic reason to work: a risk premium someone is paid for, a behavioural bias, a structural
flow, an institutional constraint. Say who is on the other side and why the effect should survive trading costs.
Avoid ideas that trade every day unless the reason is strong: each trade costs money.

You can only express ideas in this JSON grammar. Anything else is rejected. No code, no prose outside JSON.
"""


def grammar_card(space: Space, scope_text: str) -> str:
    """The grammar explained for the model, for one space.

    Args:
        space: The space proposals must fit (bans removed from the lists).
        scope_text: What the scope is, e.g. "SPY, a US equity ETF, daily bars".

    Returns:
        Text for the prompt.
    """
    terms = ", ".join(f"{t} ({formula.TERMINALS[t]})" for t in space.open_terminals)
    ops = {"unary (x)": formula.UNARY, "binary (x, y)": formula.BINARY, "time series (x, d)": formula.TS,
           "two series (x, y, d)": formula.TS2}
    if space.kind == "panel":
        ops["across instruments (x)"] = formula.CS
    op_lines = "\n".join(f"  - {k}: {', '.join(o for o in v if space.allowed(o))}" for k, v in ops.items())
    rules = ", ".join(r for r in space.rules if space.allowed(r))
    common = (f"Scope: {scope_text}.\n"
              f"Formula terminals: {terms}.\n"
              f"Operators (d is a whole number of days, 1-{formula.MAX_WINDOW}):\n{op_lines}\n"
              f"Formula nodes look like {{\"op\": \"ts_mean\", \"x\": {{\"op\": \"ret\"}}, \"d\": 20}}; at most "
              f"{formula.MAX_NODES} nodes and depth {formula.MAX_DEPTH}. Every operator uses only past and current "
              f"data.\nExisting rules you may reuse with your own parameters: {rules}.\n")
    if space.kind == "bars":
        shape = """A spec:
{"signal": {"formula": <node>} or {"rule": "<name>", "params": {...}},
 "direction": "long" or "long_short" (formulas: long when the formula is above 0; short below it if long_short),
 "filter": null or {"kind": "trend" or "calm", "window": days},
 "exit": null or {"kind": "time", "days": n} or {"kind": "trail", "pct": 0.01-0.5},
 "session": "all" """ + (""" or "overnight" (buy at the close, sell at the next open) or "day" (open to close)"""
                         if space.sessions else "") + """,
 "sizing": null or {"kind": "vol_target", "target": 0.02-0.5}}
Example: {"signal": {"formula": {"op": "neg", "x": {"op": "ts_zscore", "x": {"op": "ret"}, "d": 5}}},
 "direction": "long", "filter": {"kind": "trend", "window": 200}, "exit": {"kind": "time", "days": 5},
 "session": "all", "sizing": null}"""
    else:
        shape = """A spec:
{"signal": {"formula": <node>} or {"rule": "<name>", "params": {...}},
 "hold": {"quantile": 0.05-0.5, "rebalance": days} (formulas: hold that share of members with the highest score,
   equal weights, long only),
 "filter": null or {"kind": "trend" or "calm", "window": days},
 "sizing": null or {"kind": "vol_target", "target": 0.02-0.5}}
Example: {"signal": {"formula": {"op": "neg", "x": {"op": "cs_rank", "x": {"op": "ts_std", "x": {"op": "ret"},
 "d": 20}}}}, "hold": {"quantile": 0.2, "rebalance": 7}, "filter": null, "sizing": null}"""
    return common + shape


def check_rationale(text: Any) -> str:
    """Check a proposal's rationale.

    Args:
        text: The model's rationale.

    Returns:
        The rationale, stripped.

    Raises:
        ValueError: Not a string, or shorter than ``MIN_RATIONALE`` or longer than ``MAX_RATIONALE`` characters.
    """
    if not isinstance(text, str) or not MIN_RATIONALE <= len(text.strip()) <= MAX_RATIONALE:
        raise ValueError(f"rationale must be a string of {MIN_RATIONALE}-{MAX_RATIONALE} characters explaining why "
                         "the idea should earn money after costs")
    return text.strip()


class Proposer:
    """Asks a model for proposals and keeps only those that fit the grammar.

    Attributes:
        llm: The provider (``qlab.inbox.llm``).
        scope_text: The scope in words.
        accepted: Every accepted proposal: ``strategy``, ``params``, ``text``, ``rationale`` and ``key`` (the spec's
            identity, ``search.key``).
        outside: Ideas the model couldn't express in the grammar.
        rejected: Count of proposals that failed validation.
        calls: Model calls made.
    """

    def __init__(self, scope_text: str, llm: models.Provider | None = None, hint: str = "") -> None:
        """Set up a proposer.

        Args:
            scope_text: The scope in words.
            llm: Provider; None uses the inbox's default (local Ollama).
            hint: The owner's research direction, added to the prompt (e.g. "volume and liquidity effects").
        """
        self.llm = llm or models.provider()
        self.scope_text, self.hint = scope_text, hint
        self.accepted: list[dict[str, Any]] = []
        self.outside: list[str] = []
        self.rejected = 0
        self.calls = 0
        self._shown: list[str] = []

    def _ask(self, space: Space, n: int, feedback: str) -> dict[str, Any]:
        """One model call.

        Args:
            space: The space.
            n: Proposals wanted.
            feedback: Reasons earlier proposals were rejected, or "".

        Returns:
            The parsed answer.

        Raises:
            models.LLMError: The model failed or the budget is spent.
        """
        triage.check_budget(self.llm)
        avoid = "\n".join(f"- {t}" for t in self._shown[-30:])
        prompt = (grammar_card(space, self.scope_text)
                  + f"\n\nPropose {n} distinct ideas as {{\"proposals\": [{{\"rationale\": ..., \"spec\": ...}}], "
                  "\"outside\": [ideas you can't express in this grammar, one sentence each]}."
                  + (f"\nResearch direction from the owner: {self.hint}" if self.hint else "")
                  + (f"\nAlready proposed (don't repeat):\n{avoid}" if avoid else "")
                  + (f"\nYour previous answer had invalid proposals:\n{feedback}\nFix them." if feedback else ""))
        self.calls += 1
        try:
            answer = self.llm.ask_json(SYSTEM, prompt, SCHEMA)
        except models.LLMError:
            triage.log_call("mining", None, self.llm, 0.0, False)
            raise
        triage.log_call("mining", answer, self.llm, answer.seconds, True)
        return answer.data

    def rationale_of(self, spec: dict[str, Any]) -> str | None:
        """The rationale given with a spec, if the model proposed it.

        Args:
            spec: A normalised spec.

        Returns:
            The rationale, or None.
        """
        k = json.dumps(spec, sort_keys=True)
        return next((a["rationale"] for a in self.accepted if a["key"] == k), None)

    def propose(self, space: Space, n: int) -> list[tuple[dict[str, Any], str]]:
        """Up to ``n`` valid proposals, retrying once with the reasons for any that were rejected.

        Args:
            space: The space proposals must fit.
            n: Proposals wanted.

        Returns:
            ``(spec, rationale)`` pairs, specs normalised.

        Raises:
            models.LLMError: The model failed or the budget is spent.
        """
        out: list[tuple[dict[str, Any], str]] = []
        feedback = ""
        for _ in range(2):
            data = self._ask(space, n - len(out), feedback)
            errors = []
            for i, item in enumerate(data.get("proposals") or []):
                try:
                    if not isinstance(item, dict):
                        raise ValueError("each proposal must be an object with rationale and spec")
                    why = check_rationale(item.get("rationale"))
                    spec = grammar.validate_spec(item.get("spec"), space)
                except ValueError as err:
                    self.rejected += 1
                    errors.append(f"proposal {i + 1}: {err}")
                    continue
                name, _, params = grammar.split(spec, space)
                self.accepted.append({"strategy": name, "params": params, "text": grammar.describe(spec),
                                      "rationale": why, "key": json.dumps(spec, sort_keys=True)})
                self._shown.append(grammar.describe(spec))
                out.append((spec, why))
            self.outside += [str(x)[:300] for x in (data.get("outside") or []) if isinstance(x, str)][:10]
            if not errors or len(out) >= n:
                break
            feedback = "\n".join(errors[:10])
        return out[:n]


def describe_scope(scope: Any, space: Space) -> str:
    """A scope in words for the prompt.

    Args:
        scope: An ``evaluate.Scope``.
        space: Its space.

    Returns:
        E.g. "SPY (yahoo), one instrument, daily bars, 252 trading days a year".
    """
    if space.kind == "bars":
        return (f"{scope.name} ({scope.source}), one instrument, daily bars, {space.dpy} trading days a year, "
                f"costs about {'15' if space.dpy == 365 else '3'} bps per trade")
    return (f"universe {scope.name}, daily, long-only weights across its members"
            + (", members chosen each day by 30-day dollar volume (crypto, 20 bps per trade)" if space.dynamic
               else ", every member trades every day"))


def dumps(proposals: list[tuple[dict[str, Any], str]]) -> str:
    """Proposals as JSON, for logs.

    Args:
        proposals: ``(spec, rationale)`` pairs.

    Returns:
        A JSON string.
    """
    return json.dumps([{"spec": s, "rationale": r} for s, r in proposals])
