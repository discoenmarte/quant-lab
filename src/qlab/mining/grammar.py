"""The mining grammar: signals × filters × exits × sizing, compiled into strategies the gauntlet can judge.

A mined candidate is a **spec**, a small JSON document:

- ``signal``: a rule the lab already has (``{"rule": "trend_sma", "params": {...}}``), or a formula
  (``{"formula": <tree>}``, see ``formula``).
- ``filter``: none, ``trend`` (trade only on the side of a moving average) or ``calm`` (trade only while recent
  volatility is at or below its one-year median).
- ``exit`` (one instrument): none, ``time`` (leave after a number of days) or ``trail`` (leave after a fall of some
  percent from the best close since entry). After an exit the rule waits for its signal to reset.
- ``session`` (one instrument with real opening auctions): hold all day, only overnight, or only during the day.
- ``sizing``: none, or ``vol_target`` (scale down to a volatility target; never above full size).
- ``direction`` (formulas on one instrument): long or long-short. ``hold`` (formulas on a universe): the share of
  members held, best scores first, and the days between rebalances.

Design:

- **One space per scope.** A ``Space`` says what a scope offers: one instrument or a universe, which terminals,
  which of the lab's rules fit, whether sessions make sense (crypto trades around the clock, so its open is the
  previous close). The owner can ban parts of it (a feature family, an operator, a component).
- **Compiled only through this module.** ``validate_spec`` checks untrusted specs (from the language model)
  against the space; ``compile_spec`` turns a spec into a ``Strategy`` or ``PortfolioStrategy`` whose function
  evaluates the spec. Nothing generated is ever executed as code.
- **Tunable parameters.** Up to ``MAX_TUNABLE`` numbers of a spec (signal windows first) become the strategy's
  parameters, with a range around the mined value, so the gauntlet's grid, walk-forward and plateau stages vary
  them like any family's parameters. The other numbers are fixed.
- **Names.** A spec's structure (its numbers replaced by parameter names) hashes to the strategy name
  ``mined_<hash>``, and its parameters are the numbers. So the ledger keys mined trials the way it keys every
  trial: strategy plus parameters.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .. import db
from ..backtest.engine import SESSIONS
from ..backtest.strategies import REGISTRY as BAR_REGISTRY
from ..backtest.strategies import Param, Strategy
from ..portfolio.panel import Panel
from ..portfolio.strategies import REGISTRY as PANEL_REGISTRY
from ..portfolio.strategies import PortfolioStrategy, _bucket, _ex_ante_vol, _hold
from . import formula

FAMILY = "mined"
BAR_RULES = ("trend_sma", "sma_cross", "tsmom", "donchian", "rsi_reversion")
# Only timing and selection rules: volatility-scaling rules (risk parity, volatility management) are sizing, which the
# grammar has as its own component.
STATIC_RULES = ("tsmom", "trend_filter", "xs_momentum")
DYNAMIC_RULES = ("xs_weekly_momentum", "xs_reversal", "xs_low_vol", "xs_small_size")
FILTER_TREND = (50, 100, 150, 200)
FILTER_CALM = (21, 63)
EXIT_DAYS = (5, 10, 20, 40, 60)
EXIT_PCT = (0.05, 0.1, 0.15, 0.2)
SIZE_VOL = (0.1, 0.15, 0.2)
QUANTILES = (0.1, 0.2, 0.3, 0.5)
REBALANCE = (1, 5, 7, 14, 21, 42, 63)
VOL_WINDOW = 63      # days of returns behind the volatility estimate of ``vol_target``
CALM_MEDIAN = 252    # days over which ``calm`` takes the median of recent volatility
MAX_TUNABLE = 4      # the gauntlet's grid keeps every setting one step from the candidate: 3^4 = 81 of them
# Parts of the grammar the owner can ban, besides operators and terminals.
COMPONENTS = ("rule", "formula", "trend", "calm", "time", "trail", "vol_target", "overnight", "day", "long_short")


@dataclass(frozen=True)
class Space:
    """What a scope lets the miner build.

    Attributes:
        kind: ``bars`` (one instrument) or ``panel`` (a universe).
        terminals: Formula terminals available.
        rules: The lab's rules that fit the scope.
        dynamic: A universe whose members change (cross-sectional crypto).
        dpy: Trading days per year (252 or 365).
        sessions: Whether overnight-only and day-only holding make sense (real opening auctions).
        banned: Operators, terminals, rules or components (``COMPONENTS``) the search must leave out.
    """

    kind: str
    terminals: tuple[str, ...]
    rules: tuple[str, ...]
    dynamic: bool = False
    dpy: int = 252
    sessions: bool = False
    banned: tuple[str, ...] = ()

    def allowed(self, name: str) -> bool:
        """Whether a part of the grammar may be used.

        Args:
            name: Operator, terminal, rule or component name.

        Returns:
            False when the owner banned it.
        """
        return name not in self.banned

    @property
    def open_terminals(self) -> tuple[str, ...]:
        """Terminals left after bans."""
        return tuple(t for t in self.terminals if self.allowed(t))

    def to_dict(self) -> dict[str, Any]:
        """The space as JSON-ready data."""
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Space:
        """Rebuild a space saved with ``to_dict``.

        Args:
            d: Saved space.

        Returns:
            The space.
        """
        return cls(kind=d["kind"], terminals=tuple(d["terminals"]), rules=tuple(d["rules"]),
                   dynamic=bool(d.get("dynamic")), dpy=int(d.get("dpy", 252)), sessions=bool(d.get("sessions")),
                   banned=tuple(d.get("banned", ())))


def bars_space(asset_class: str, dpy: int, banned: tuple[str, ...] = ()) -> Space:
    """The space of one instrument.

    Args:
        asset_class: ``equity`` or ``crypto``. Crypto has no closed market overnight, so no gap and no sessions.
        dpy: Trading days per year.
        banned: Parts to leave out.

    Returns:
        The space.
    """
    crypto = asset_class == "crypto"
    terms = ("open", "high", "low", "close", "volume", "ret") + (() if crypto else ("gap",)) + formula.CALENDAR
    return Space("bars", terms, BAR_RULES, False, dpy, not crypto, tuple(banned))


def panel_space(panel: Panel, banned: tuple[str, ...] = ()) -> Space:
    """The space of a universe.

    Args:
        panel: The universe's panel.
        banned: Parts to leave out.

    Returns:
        The space: dollar volume only where the panel has it (the crypto cross-section), the overnight gap only
        where every member has a real open (not crypto).
    """
    crypto = any(a == "crypto" for a in panel.asset_class)
    terms = ("open", "close", "ret", "mkt") + (() if crypto else ("gap",)) + (("dvol",) if panel.dynamic else ())
    rules = DYNAMIC_RULES if panel.dynamic else STATIC_RULES
    return Space("panel", terms + formula.CALENDAR, rules, panel.dynamic, panel.dpy, False, tuple(banned))


def rule_of(space: Space, name: str) -> Strategy | PortfolioStrategy:
    """A registered rule of the space's kind.

    Args:
        space: The space.
        name: Rule key.

    Returns:
        The registered strategy.
    """
    return BAR_REGISTRY[name] if space.kind == "bars" else PANEL_REGISTRY[name]


def _grid(p: Param) -> list[float]:
    """Every value of a registered parameter's grid.

    Args:
        p: The parameter.

    Returns:
        ``low, low + step, ..., high``; whole numbers as int.
    """
    n = int(round((p.high - p.low) / p.step))
    vals = [p.low + i * p.step for i in range(n + 1)]
    return [int(round(v)) if p.is_int else round(float(v), 10) for v in vals]


# ---------------------------------------------------------------------------------------------------------------
# Sampling


def sample_spec(ch: formula.Chooser, space: Space) -> dict[str, Any]:
    """Draw a random spec from the space, every decision through ``ch``.

    Decision names are fixed per space, so an Optuna study sees one distribution per name; the random and Bayesian
    searches and the genetic programme's first generation all draw from this one function.

    Args:
        ch: Source of choices.
        space: The space.

    Returns:
        A spec in canonical form.
    """
    kinds = [k for k in ("rule", "formula") if space.allowed(k) and (k == "formula" or space.rules)]
    spec: dict[str, Any] = {"kind": space.kind}
    if ch.choice("signal", kinds) == "rule":
        name = ch.choice("rule", [r for r in space.rules if space.allowed(r)])
        strat = rule_of(space, name)
        spec["signal"] = {"rule": name, "params": {k: ch.choice(f"rule.{name}.{k}", _grid(p))
                                                    for k, p in strat.params.items()}}
    else:
        spec["signal"] = {"formula": formula.sample(_Banned(ch, space), space.open_terminals,
                                                    space.kind == "panel")}
        if space.kind == "bars":
            spec["direction"] = ch.choice("direction", [d for d in ("long", "long_short") if space.allowed(d)])
        else:
            spec["hold"] = {"quantile": ch.choice("hold.quantile", list(QUANTILES)),
                            "rebalance": ch.choice("hold.rebalance", list(REBALANCE))}
    filt = ch.choice("filter", [f for f in ("none", "trend", "calm") if space.allowed(f)])
    spec["filter"] = None if filt == "none" else {
        "kind": filt, "window": ch.choice(f"filter.{filt}", list(FILTER_TREND if filt == "trend" else FILTER_CALM))}
    if space.kind == "bars":
        ex = ch.choice("exit", [e for e in ("none", "time", "trail") if space.allowed(e)])
        spec["exit"] = (None if ex == "none" else {"kind": "time", "days": ch.choice("exit.days", list(EXIT_DAYS))}
                        if ex == "time" else {"kind": "trail", "pct": ch.choice("exit.pct", list(EXIT_PCT))})
        spec["session"] = (ch.choice("session", [s for s in SESSIONS if s == "all" or space.allowed(s)])
                           if space.sessions else "all")
    size = ch.choice("sizing", [s for s in ("none", "vol_target") if space.allowed(s)])
    spec["sizing"] = None if size == "none" else {"kind": "vol_target",
                                                   "target": ch.choice("sizing.target", list(SIZE_VOL))}
    return normalize(spec, space)


class _Banned:
    """A chooser that hides banned operators from the formula sampler.

    Attributes:
        ch: The underlying chooser.
        space: The space whose bans apply.
    """

    def __init__(self, ch: formula.Chooser, space: Space) -> None:
        """Wrap a chooser.

        Args:
            ch: The underlying chooser.
            space: The space whose bans apply.
        """
        self.ch, self.space = ch, space

    def choice(self, name: str, options: list[Any]) -> Any:
        """Pick among the options that aren't banned (all of them when every one is).

        Args:
            name: Decision name.
            options: Choices.

        Returns:
            The pick.
        """
        kept = [o for o in options if not isinstance(o, str) or self.space.allowed(o)]
        return self.ch.choice(name, kept or options)


class RandomChooser:
    """Uniform choices from a numpy generator.

    Attributes:
        rng: The generator.
    """

    def __init__(self, rng: np.random.Generator) -> None:
        """Wrap a generator.

        Args:
            rng: The generator.
        """
        self.rng = rng

    def choice(self, name: str, options: list[Any]) -> Any:
        """Pick one option uniformly.

        Args:
            name: Decision name (unused).
            options: Choices.

        Returns:
            The pick.
        """
        return options[int(self.rng.integers(len(options)))]


# ---------------------------------------------------------------------------------------------------------------
# Validation of untrusted specs


def _num(v: Any, where: str, low: float, high: float, whole: bool = False) -> float:
    """Check a number from an untrusted spec.

    Args:
        v: The value.
        where: Where it sits, for the message.
        low: Smallest allowed.
        high: Largest allowed.
        whole: Must be a whole number.

    Returns:
        The value as int (``whole``) or float.

    Raises:
        ValueError: Not a number, out of range, or not whole when it must be.
    """
    if isinstance(v, bool) or not isinstance(v, int | float) or not np.isfinite(v):
        raise ValueError(f"{where} must be a number")
    if not low <= v <= high:
        raise ValueError(f"{where} must be between {low:g} and {high:g}")
    if whole and float(v) != int(v):
        raise ValueError(f"{where} must be a whole number")
    return int(v) if whole else float(v)


def _keys(d: Any, where: str, allowed: set[str], required: set[str] = frozenset()) -> dict[str, Any]:
    """Check an untrusted object's keys.

    Args:
        d: The value.
        where: Where it sits, for the message.
        allowed: Keys it may have.
        required: Keys it must have.

    Returns:
        The dict.

    Raises:
        ValueError: Not an object, unknown keys, or missing keys.
    """
    if not isinstance(d, dict):
        raise ValueError(f"{where} must be an object")
    extra, missing = set(d) - allowed, set(required) - set(d)
    if extra:
        raise ValueError(f"{where} has unknown keys {sorted(extra)}; allowed: {sorted(allowed)}")
    if missing:
        raise ValueError(f"{where} is missing {sorted(missing)}")
    return d


def validate_spec(spec: Any, space: Space) -> dict[str, Any]:
    """Check an untrusted spec against a space and return it in canonical form.

    Args:
        spec: Parsed JSON, e.g. from a language model.
        space: The space it must fit, bans included.

    Returns:
        The spec, normalised (``normalize``).

    Raises:
        ValueError: Anything outside the grammar, with a message saying what and where.
    """
    top = {"kind", "signal", "filter", "sizing"} | ({"exit", "session", "direction"} if space.kind == "bars"
                                                     else {"hold"})
    s = _keys(spec, "spec", top, {"signal"})
    if s.get("kind", space.kind) != space.kind:
        raise ValueError(f"spec.kind must be {space.kind!r} for this scope")
    sig = _keys(s["signal"], "signal", {"rule", "params", "formula"})
    if ("rule" in sig) == ("formula" in sig):
        raise ValueError("signal needs exactly one of 'rule' (with 'params') or 'formula'")
    if "rule" in sig:
        if not space.allowed("rule"):
            raise ValueError("rules are banned in this job")
        name = sig["rule"]
        if name not in space.rules or not space.allowed(name):
            raise ValueError(f"signal.rule must be one of {', '.join(r for r in space.rules if space.allowed(r))}")
        strat = rule_of(space, name)
        params = _keys(sig.get("params", {}), "signal.params", set(strat.params))
        for k, v in params.items():
            p = strat.params[k]
            _num(v, f"signal.params.{k}", p.low, p.high, p.is_int)
    else:
        if not space.allowed("formula") or "params" in sig:
            raise ValueError("formulas are banned in this job" if not space.allowed("formula")
                             else "signal.params goes with a rule, not a formula")
        formula.validate(sig["formula"], space.open_terminals, space.kind == "panel")
        banned_ops = [o for o in formula.ops_used(sig["formula"]) if not space.allowed(o)]
        if banned_ops:
            raise ValueError(f"formula uses banned operators {sorted(set(banned_ops))}")
    f = s.get("filter")
    if f is not None:
        _keys(f, "filter", {"kind", "window"}, {"kind", "window"})
        if f["kind"] not in ("trend", "calm") or not space.allowed(f["kind"]):
            raise ValueError("filter.kind must be 'trend' or 'calm' (or filter null)")
        _num(f["window"], "filter.window", 2, formula.MAX_WINDOW, True)
    z = s.get("sizing")
    if z is not None:
        _keys(z, "sizing", {"kind", "target"}, {"kind", "target"})
        if z["kind"] != "vol_target" or not space.allowed("vol_target"):
            raise ValueError("sizing.kind must be 'vol_target' (or sizing null)")
        _num(z["target"], "sizing.target", 0.02, 0.5)
    if space.kind == "bars":
        e = s.get("exit")
        if e is not None:
            _keys(e, "exit", {"kind", "days", "pct"}, {"kind"})
            if e["kind"] == "time" and space.allowed("time"):
                _keys(e, "exit", {"kind", "days"}, {"kind", "days"})
                _num(e["days"], "exit.days", 1, 252, True)
            elif e["kind"] == "trail" and space.allowed("trail"):
                _keys(e, "exit", {"kind", "pct"}, {"kind", "pct"})
                _num(e["pct"], "exit.pct", 0.01, 0.5)
            else:
                raise ValueError("exit.kind must be 'time' (with days) or 'trail' (with pct), or exit null")
        session = s.get("session", "all")
        if session not in SESSIONS or (session != "all" and (not space.sessions or not space.allowed(session))):
            raise ValueError("session must be 'all'" + (", 'overnight' or 'day'" if space.sessions else
                                                        " here (this market never closes)"))
        if s.get("direction", "long") not in ("long", "long_short") or (
                s.get("direction") == "long_short" and not space.allowed("long_short")):
            raise ValueError("direction must be 'long' or 'long_short'")
    elif "formula" in sig:
        h = _keys(s.get("hold", {"quantile": 0.2, "rebalance": 21}), "hold", {"quantile", "rebalance"},
                  {"quantile", "rebalance"})
        _num(h["quantile"], "hold.quantile", 0.05, 0.5)
        _num(h["rebalance"], "hold.rebalance", 1, 126, True)
    elif "hold" in s:
        raise ValueError("hold goes with a formula; a rule rebalances on its own schedule")
    return normalize(s, space)


def normalize(spec: dict[str, Any], space: Space) -> dict[str, Any]:
    """A spec in canonical form: every component present, numbers typed, formula canonical.

    Args:
        spec: A valid spec, possibly with optional keys missing.
        space: Its space.

    Returns:
        A new dict with keys in a fixed order.
    """
    sig = spec["signal"]
    out: dict[str, Any] = {"kind": space.kind}
    if "rule" in sig:
        strat = rule_of(space, sig["rule"])
        merged = {**strat.defaults(), **sig.get("params", {})}
        out["signal"] = {"rule": sig["rule"], "params": {k: (int(merged[k]) if p.is_int else float(merged[k]))
                                                         for k, p in strat.params.items()}}
    else:
        out["signal"] = {"formula": formula.canonical(sig["formula"])}
    f = spec.get("filter")
    out["filter"] = None if f is None else {"kind": f["kind"], "window": int(f["window"])}
    if space.kind == "bars":
        if "formula" in sig:
            out["direction"] = spec.get("direction", "long")
        e = spec.get("exit")
        out["exit"] = (None if e is None else {"kind": "time", "days": int(e["days"])} if e["kind"] == "time"
                       else {"kind": "trail", "pct": float(e["pct"])})
        out["session"] = spec.get("session", "all")
    elif "formula" in sig:
        h = spec.get("hold", {"quantile": 0.2, "rebalance": 21})
        out["hold"] = {"quantile": float(h["quantile"]), "rebalance": int(h["rebalance"])}
    z = spec.get("sizing")
    out["sizing"] = None if z is None else {"kind": "vol_target", "target": float(z["target"])}
    return out


# ---------------------------------------------------------------------------------------------------------------
# Tunable parameters, names and plain English


def _around(v: float, whole: bool, step: float, low: float, high: float, doc: str) -> Param:
    """A parameter range two steps either side of a mined value.

    Args:
        v: The mined value.
        whole: Whether the parameter is a count of days.
        step: Grid step.
        low: Smallest value allowed.
        high: Largest value allowed.
        doc: What it means.

    Returns:
        The ``Param``.
    """
    if whole:
        return Param(int(v), int(max(low, v - 2 * step)), int(min(high, v + 2 * step)), int(step), doc)
    return Param(float(v), round(max(low, v - 2 * step), 10), round(min(high, v + 2 * step), 10), step, doc)


def _window_param(v: int, lowest: int, doc: str) -> Param:
    """A window parameter: steps of about a fifth of the value.

    Args:
        v: The mined window.
        lowest: Smallest window allowed.
        doc: What it means.

    Returns:
        The ``Param``.
    """
    return _around(v, True, max(1, round(v / 5)), lowest, formula.MAX_WINDOW, doc)


def tunables(spec: dict[str, Any], space: Space) -> list[tuple[str, list[str], Param]]:
    """The spec's numbers that become strategy parameters, most important first, at most ``MAX_TUNABLE``.

    Args:
        spec: A normalised spec.
        space: Its space.

    Returns:
        ``(name, key path into the spec, Param)`` triples. Names are valid Python identifiers.
    """
    out: list[tuple[str, list[str], Param]] = []
    sig = spec["signal"]
    if "rule" in sig:
        strat = rule_of(space, sig["rule"])
        for k, p in strat.params.items():
            v = sig["params"][k]
            out.append((k, ["signal", "params", k], Param(v, min(p.low, v), max(p.high, v), p.step, p.doc)))
    else:
        node = sig["formula"]
        for i, path in enumerate(formula.windows(node), 1):
            sub = node
            for key in path[:-1]:
                sub = sub[key]
            lowest = 1 if sub["op"] in formula.LAG_OPS else 2
            out.append((f"w{i}", ["signal", "formula", *path], _window_param(sub["d"], lowest,
                                                                              f"Window of {sub['op']}, days")))
    if spec.get("hold"):
        r = spec["hold"]["rebalance"]
        out.append(("rebalance", ["hold", "rebalance"], _around(r, True, max(1, round(r / 3)), 1, 126,
                                                                "Days between rebalances")))
    if spec.get("filter"):
        f = spec["filter"]
        out.append(("filter_window", ["filter", "window"],
                    _window_param(f["window"], 2, f"{f['kind']} filter window, days")))
    e = spec.get("exit")
    if e and e["kind"] == "time":
        out.append(("exit_days", ["exit", "days"], _window_param(e["days"], 1, "Days held before the time exit")))
    elif e:
        out.append(("exit_pct", ["exit", "pct"], _around(e["pct"], False, 0.05, 0.01, 0.5,
                                                         "Fall from the best close that exits")))
    if spec.get("sizing"):
        out.append(("size_vol", ["sizing", "target"], _around(spec["sizing"]["target"], False, 0.05, 0.02, 0.5,
                                                              "Volatility target, annual")))
    if spec.get("hold"):
        out.append(("quantile", ["hold", "quantile"], _around(spec["hold"]["quantile"], False, 0.1, 0.05, 0.5,
                                                              "Share of members held")))
    return out[:MAX_TUNABLE]


def _set(d: dict[str, Any], path: list[str], value: Any) -> None:
    """Set a value at a key path.

    Args:
        d: Nested dicts.
        path: Keys.
        value: New value.
    """
    for key in path[:-1]:
        d = d[key]
    d[path[-1]] = value


def with_values(spec: dict[str, Any], paths: dict[str, list[str]], values: dict[str, Any]) -> dict[str, Any]:
    """A copy of a spec with some numbers replaced.

    Args:
        spec: A normalised spec.
        paths: Parameter name to key path.
        values: Parameter name to value; names not in ``paths`` are ignored.

    Returns:
        A new spec.
    """
    out = copy.deepcopy(spec)
    for name, v in values.items():
        if name in paths:
            _set(out, paths[name], v)
    return out


def split(spec: dict[str, Any], space: Space) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """A spec's strategy name, its template (the structure) and its parameter values.

    Args:
        spec: A normalised spec.
        space: Its space.

    Returns:
        ``(name, template, params)``: ``mined_`` plus a 10-hex hash of the template; the template is the spec with
        each tunable number replaced by ``"$<name>"``.
    """
    template = copy.deepcopy(spec)
    params = {}
    for name, path, p in tunables(spec, space):
        params[name] = p.default
        _set(template, path, f"${name}")
    digest = hashlib.sha1(json.dumps({"space": space.kind, "t": template}, sort_keys=True).encode()).hexdigest()
    return f"mined_{digest[:10]}", template, params


def describe(spec: dict[str, Any]) -> str:
    """A spec in plain English.

    Args:
        spec: A normalised spec.

    Returns:
        One or two sentences.
    """
    sig = spec["signal"]
    if "rule" in sig:
        args = ", ".join(f"{k} {v:g}" for k, v in sig["params"].items())
        core = f"The lab's {sig['rule']} rule ({args})"
    elif spec["kind"] == "bars":
        what = formula.describe(sig["formula"])
        core = (f"Long when {what} is above zero, "
                + ("short when it is below" if spec.get("direction") == "long_short" else "flat otherwise"))
    else:
        h = spec["hold"]
        what = formula.describe(sig["formula"])
        core = (f"Hold the top {h['quantile']:.0%} of members by {what}, in equal weights, rebalanced every "
                f"{h['rebalance']} days")
    parts = [core]
    f = spec.get("filter")
    if f and f["kind"] == "trend":
        parts.append(f"only on the side of the {f['window']}-day moving average")
    elif f:
        parts.append(f"only while {f['window']}-day volatility is at or below its one-year median")
    e = spec.get("exit")
    if e and e["kind"] == "time":
        parts.append(f"leaving after {e['days']} days")
    elif e:
        parts.append(f"leaving after a {e['pct']:.0%} fall from the best close")
    if spec.get("session") == "overnight":
        parts.append("held only overnight (bought at the close, sold at the open)")
    elif spec.get("session") == "day":
        parts.append("held only during the day (bought at the open, sold at the close)")
    if spec.get("sizing"):
        parts.append(f"scaled down to {spec['sizing']['target']:.0%} volatility")
    return "; ".join(parts) + "."


def signature(spec: dict[str, Any]) -> dict[str, Any]:
    """What a spec is made of, for coverage maps.

    Args:
        spec: A normalised spec.

    Returns:
        ``signal`` (rule name or ``formula``), ``ops``, ``terminals``, ``filter``, ``exit``, ``session``,
        ``sizing``, ``nodes`` (formula size, 1 for a rule) and ``windows`` (formula windows).
    """
    sig = spec["signal"]
    node = sig.get("formula")
    return {
        "signal": sig.get("rule", "formula"),
        "ops": formula.ops_used(node) if node else [],
        "terminals": formula.terminals_used(node) if node else [],
        "filter": (spec.get("filter") or {}).get("kind", "none"),
        "exit": (spec.get("exit") or {}).get("kind", "none"),
        "session": spec.get("session", "all"),
        "sizing": (spec.get("sizing") or {}).get("kind", "none"),
        "nodes": formula.node_count(node) if node else 1,
        "windows": [int(_get(node, p)) for p in formula.windows(node)] if node else [],
    }


def _get(d: dict[str, Any], path: list[str]) -> Any:
    """Value at a key path.

    Args:
        d: Nested dicts.
        path: Keys.

    Returns:
        The value.
    """
    for key in path:
        d = d[key]
    return d


# ---------------------------------------------------------------------------------------------------------------
# Compilation


@dataclass(frozen=True)
class MinedStrategy(Strategy):
    """A mined single-instrument rule.

    Attributes:
        spec: The spec it was compiled from.
        session: ``all``, ``overnight`` or ``day``; the engine holds only that leg of each day.
    """

    spec: dict[str, Any] = field(default_factory=dict)
    session: str = "all"


@dataclass(frozen=True)
class MinedPortfolioStrategy(PortfolioStrategy):
    """A mined portfolio rule.

    Attributes:
        spec: The spec it was compiled from.
    """

    spec: dict[str, Any] = field(default_factory=dict)


def calendar(ts: pd.DatetimeIndex | pd.Series, dpy: int) -> dict[str, np.ndarray]:
    """Calendar terminals, centred so their sign means something.

    Args:
        ts: Dates of the rows.
        dpy: 365 for markets that trade every day (weekdays run 0-6), 252 otherwise (0-4).

    Returns:
        ``dow`` (day of week minus its middle), ``tom`` (+1 from the 28th to the 3rd, the turn of the month, −1
        otherwise) and ``month`` (month minus 6.5), each ``(T,)``.
    """
    idx = pd.DatetimeIndex(ts)
    dom = idx.day.to_numpy()
    return {"dow": idx.dayofweek.to_numpy(float) - (3.0 if dpy == 365 else 2.0),
            "tom": np.where((dom >= 28) | (dom <= 3), 1.0, -1.0),
            "month": idx.month.to_numpy(float) - 6.5}


def bar_inputs(bars: pd.DataFrame, dpy: int) -> dict[str, np.ndarray]:
    """Terminals for one instrument, as ``(T, 1)`` arrays from raw prices (what an order would see).

    Args:
        bars: Normalised bars.
        dpy: Trading days per year.

    Returns:
        Terminal name to array.
    """
    def col(k: str) -> np.ndarray:
        return bars[k].to_numpy(float)[:, None] if k in bars else np.full((len(bars), 1), np.nan)

    c = col("close")
    prev = np.vstack([np.full((1, 1), np.nan), c[:-1]])
    with np.errstate(all="ignore"):
        out = {"open": col("open"), "high": col("high"), "low": col("low"), "close": c, "volume": col("volume"),
               "ret": c / prev - 1.0, "gap": col("open") / prev - 1.0}
    out.update({k: v[:, None] for k, v in calendar(bars["ts"], dpy).items()})
    return out


def panel_inputs(panel: Panel) -> dict[str, np.ndarray]:
    """Terminals for a universe, ``(T, N)`` arrays.

    Args:
        panel: The panel.

    Returns:
        Terminal name to array; ``mkt`` is the average daily return of the members, the same in every column.
    """
    r = panel.returns()
    prev = np.vstack([np.full((1, panel.n), np.nan), panel.close[:-1]])
    with np.errstate(all="ignore"):
        mkt = np.nanmean(np.where(panel.member, r, np.nan), axis=1)
        gap = panel.open / prev - 1.0
    out = {"open": panel.open, "close": panel.close, "ret": r, "gap": gap, "dvol": panel.dollar_volume,
           "mkt": np.repeat(mkt[:, None], panel.n, axis=1)}
    out.update({k: np.repeat(v[:, None], panel.n, axis=1) for k, v in calendar(panel.ts, panel.dpy).items()})
    return out


def _exit(sides: np.ndarray, close: np.ndarray, ex: dict[str, Any] | None) -> np.ndarray:
    """Apply a time or trailing exit to a series of wanted sides.

    After an exit the rule stays out until its wanted side changes, so a stop isn't undone the next day.

    Args:
        sides: Wanted side per bar, −1, 0 or 1.
        close: Closes.
        ex: The exit, or None.

    Returns:
        Sides after exits.
    """
    if not ex:
        return sides
    out = np.zeros(len(sides))
    side, entry, best, locked = 0.0, 0, 0.0, 0.0
    days = int(ex.get("days", 0))
    pct = float(ex.get("pct", 0.0))
    for t, want in enumerate(sides):
        if locked:
            if want == locked:
                continue
            locked = 0.0
        if want != side:
            side, entry, best = want, t, close[t]
        if side == 0:
            continue
        if ex["kind"] == "time":
            stop = t - entry >= days
        else:
            best = max(best, close[t]) if side > 0 else min(best, close[t])
            stop = close[t] <= best * (1 - pct) if side > 0 else close[t] >= best * (1 + pct)
        if stop:
            locked, side = side, 0.0
            continue
        out[t] = side
    return out


def bar_positions(spec: dict[str, Any], bars: pd.DataFrame, dpy: int) -> pd.Series:
    """Positions of a single-instrument spec, each decided from data up to that bar's close.

    Args:
        spec: A normalised bars spec.
        bars: Normalised bars.
        dpy: Trading days per year.

    Returns:
        Position per bar in [−1, 1].
    """
    sig = spec["signal"]
    close = bars["close"].to_numpy(float)
    if "rule" in sig:
        raw = BAR_REGISTRY[sig["rule"]].positions(bars, sig["params"]).to_numpy(float)
    else:
        f = formula.evaluate(sig["formula"], bar_inputs(bars, dpy))[:, 0]
        with np.errstate(invalid="ignore"):
            raw = np.where(f > 0, 1.0, np.where(f < 0, -1.0 if spec.get("direction") == "long_short" else 0.0, 0.0))
    filt = spec.get("filter")
    if filt:
        c = pd.Series(close)
        if filt["kind"] == "trend":
            sma = c.rolling(filt["window"], min_periods=filt["window"]).mean().to_numpy()
            with np.errstate(invalid="ignore"):
                ok = np.where(raw > 0, close > sma, close < sma)
        else:
            vol = c.pct_change().rolling(filt["window"], min_periods=filt["window"]).std()
            med = vol.rolling(CALM_MEDIAN, min_periods=CALM_MEDIAN // 2).median()
            ok = (vol <= med).to_numpy()
        raw = np.where(ok, raw, 0.0)
    pos = _exit(np.sign(raw), close, spec.get("exit")) * np.abs(raw)
    if spec.get("sizing"):
        with np.errstate(divide="ignore", invalid="ignore"):
            vol = pd.Series(np.log(close)).diff().rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std().to_numpy()
            scale = np.minimum(1.0, spec["sizing"]["target"] / (vol * np.sqrt(dpy)))
        pos = pos * np.nan_to_num(scale)
    return pd.Series(pos, index=bars.index)


def panel_weights(spec: dict[str, Any], panel: Panel) -> np.ndarray:
    """Target weights of a universe spec, each decided from data up to that date's close.

    Args:
        spec: A normalised panel spec.
        panel: The panel.

    Returns:
        ``(T, N)`` weights; NaN rows (no decision possible yet) are held over by the rebalance schedule.
    """
    sig = spec["signal"]
    if "rule" in sig:
        w = PANEL_REGISTRY[sig["rule"]].weights(panel, sig["params"])
        every = int(sig["params"].get("rebalance", 21))
    else:
        score = formula.evaluate(sig["formula"], panel_inputs(panel), panel.member)
        w = _bucket(panel, score, spec["hold"]["quantile"], lowest=False)
        every = spec["hold"]["rebalance"]
    filt = spec.get("filter")
    if filt:
        close = pd.DataFrame(panel.close)
        if filt["kind"] == "trend":
            sma = close.rolling(filt["window"], min_periods=filt["window"]).mean().to_numpy()
            with np.errstate(invalid="ignore"):
                ok = panel.close > sma
        else:
            with np.errstate(all="ignore"):
                mkt = pd.Series(np.nanmean(np.where(panel.member, panel.returns(), np.nan), axis=1))
            vol = mkt.rolling(filt["window"], min_periods=filt["window"]).std()
            med = vol.rolling(CALM_MEDIAN, min_periods=CALM_MEDIAN // 2).median()
            ok = np.repeat((vol <= med).to_numpy()[:, None], panel.n, axis=1)
        w = np.where(np.isnan(w), np.nan, np.where(ok, w, 0.0))
    if spec.get("sizing"):
        vol = _ex_ante_vol(np.nan_to_num(w), np.nan_to_num(panel.returns()), VOL_WINDOW, panel.dpy)
        with np.errstate(divide="ignore", invalid="ignore"):
            scale = np.where(vol > 0, np.minimum(1.0, spec["sizing"]["target"] / vol), 1.0)
        w = w * np.where(np.isfinite(vol), scale, np.nan)[:, None]
    return _hold(w, every)


def compile_spec(spec: dict[str, Any], space: Space) -> MinedStrategy | MinedPortfolioStrategy:
    """Turn a valid spec into a strategy the engines and the gauntlet can run.

    Args:
        spec: A normalised spec (from ``validate_spec``, ``normalize`` or ``sample_spec``).
        space: Its space.

    Returns:
        A ``MinedStrategy`` (one instrument) or ``MinedPortfolioStrategy`` (a universe), named by ``split``, in the
        ``mined`` family, with the spec's tunable numbers as parameters (defaults: the mined values).
    """
    name, _, _ = split(spec, space)
    tun = tunables(spec, space)
    params = {n: p for n, _, p in tun}
    paths = {n: path for n, path, _ in tun}
    text = describe(spec)
    if space.kind == "bars":
        dpy = space.dpy

        def fn(bars: pd.DataFrame, **values: Any) -> pd.Series:
            return bar_positions(with_values(spec, paths, values), bars, dpy)

        return MinedStrategy(name, FAMILY, text, fn, params, "mined (qlab.mining)", spec=spec,
                             session=spec.get("session", "all"))

    def wfn(panel: Panel, **values: Any) -> np.ndarray:
        return panel_weights(with_values(spec, paths, values), panel)

    sig = spec["signal"]
    if "rule" in sig:
        kind = PANEL_REGISTRY[sig["rule"]].kind
    else:
        kind = "dynamic" if "dvol" in formula.terminals_used(sig["formula"]) else "any"
    return MinedPortfolioStrategy(name, FAMILY, text, wfn, params, "mined (qlab.mining)", kind, spec=spec)


# ---------------------------------------------------------------------------------------------------------------
# Storage: mined structures are kept so any trial or card can be rebuilt from its strategy name.


def save_specs(items: list[tuple[dict[str, Any], Space]], job_id: str = "") -> None:
    """Store the specs behind strategy names, first one per name.

    Args:
        items: ``(spec, space)`` pairs.
        job_id: Mining job that found them.
    """
    rows = {}
    for spec, space in items:
        name, template, _ = split(spec, space)
        rows.setdefault(name, (name, space.kind, db.dumps(space.to_dict()), db.dumps(template), db.dumps(spec),
                               describe(spec), job_id))
    if not rows:
        return
    frame = pd.DataFrame(list(rows.values()), columns=["strategy", "kind", "space", "template", "spec", "text",
                                                       "job_id"])
    with db.connect() as con:
        con.register("new_specs", frame)
        con.execute("""INSERT INTO mining_specs (strategy, kind, space, template, spec, text, job_id, created_at)
                       SELECT n.strategy, n.kind, n.space, n.template, n.spec, n.text, n.job_id, now()
                       FROM new_specs n WHERE n.strategy NOT IN (SELECT strategy FROM mining_specs)""")
        con.unregister("new_specs")


def stored(name: str) -> tuple[dict[str, Any], Space]:
    """The spec and space stored for a strategy name.

    Args:
        name: ``mined_<hash>``.

    Returns:
        ``(spec, space)``.

    Raises:
        KeyError: Nothing stored under that name.
    """
    rows = db.rows("SELECT spec, space FROM mining_specs WHERE strategy = ?", [name])
    if not rows:
        raise KeyError(f"no mined strategy {name}")
    return json.loads(rows[0]["spec"]), Space.from_dict(json.loads(rows[0]["space"]))


def load_strategy(name: str) -> MinedStrategy | MinedPortfolioStrategy:
    """Rebuild a mined strategy from its name, for report cards, the lockbox and the paper book.

    Args:
        name: ``mined_<hash>``.

    Returns:
        The compiled strategy; its parameters' defaults are the stored spec's values.

    Raises:
        KeyError: Unknown name.
    """
    spec, space = stored(name)
    return compile_spec(spec, space)

