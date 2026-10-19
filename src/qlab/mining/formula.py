"""A small formula language for mined signals: Kakushadze-style operators on prices and volume.

A formula is a JSON tree of operators. Every operator reads only the current row and earlier ones: time-series
operators look back over a window of past rows, cross-sectional operators compare instruments on the same row, and
nothing moves data from later rows to earlier ones. Look-ahead isn't something a formula can express; the
leakage checks confirm it on every mined rule anyway (Gençay, 2026: deflation can't catch leakage, so leakage has to
be impossible by construction).

Design:

- **Untrusted input.** Formulas come from search code and from a language model, so ``validate`` checks every node
  against a whitelist of operators, terminals, keys and window bounds before anything is evaluated. Nothing but these
  operators is ever executed.
- **Arrays are (T, N):** one column for a single instrument, one per instrument for a panel. Infinities become NaN,
  and NaN (warm-up, missing bars) means "no signal". A window needs a value on every row it covers.
- **Cross-sectional operators** compare the instruments that are members of the universe on that row (point in
  time), so a formula never depends on which instruments the panel happens to hold later.
- **Size is capped** (``MAX_DEPTH``, ``MAX_NODES``): mined formulas that grow without bound fit noise and decay
  (Becker & Seshadri, 2003; AlphaAgent, 2025).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np
import pandas as pd

# Terminals: the data a formula can read, with how they read in plain words.
TERMINALS: dict[str, str] = {
    "open": "the open",
    "high": "the high",
    "low": "the low",
    "close": "the close",
    "volume": "volume",
    "ret": "the daily return",
    "dvol": "dollar volume",
    "mkt": "the market's daily return",
    "gap": "the overnight gap",
    "dow": "the day of the week",
    "tom": "the turn of the month",
    "month": "the month",
}
# Calendar terminals are known in advance; ``mkt`` is the average return of the universe's members that day.
CALENDAR = ("dow", "tom", "month")
UNARY = ("neg", "abs", "sign", "slog")
BINARY = ("add", "sub", "mul", "div")
TS = ("delay", "delta", "ts_ret", "ts_mean", "ts_std", "ts_min", "ts_max", "ts_rank", "ts_zscore", "ts_sum")
TS2 = ("ts_corr",)
CS = ("cs_rank", "cs_zscore", "cs_demean")
OPS = (*UNARY, *BINARY, *TS, *TS2, *CS)
LAG_OPS = ("delay", "delta", "ts_ret")  # these accept a window of one row; the rest need two
WINDOWS = (2, 3, 5, 10, 20, 40, 60, 120, 250)  # the ladder search draws windows from, in rows (days)
MAX_WINDOW = 400
MAX_DEPTH = 4
MAX_NODES = 12
EPS = 1e-12


class Chooser(Protocol):
    """Where a sampler gets its choices: a random generator, or an Optuna trial (define-by-run)."""

    def choice(self, name: str, options: list[Any]) -> Any:
        """Pick one of ``options`` for the decision called ``name``."""
        ...


def node_count(node: dict[str, Any]) -> int:
    """Number of nodes in a formula.

    Args:
        node: A formula tree.

    Returns:
        The count, the root included.
    """
    return 1 + sum(node_count(node[k]) for k in ("x", "y") if k in node)


def depth(node: dict[str, Any]) -> int:
    """Depth of a formula; a lone terminal has depth 1.

    Args:
        node: A formula tree.

    Returns:
        The depth.
    """
    return 1 + max((depth(node[k]) for k in ("x", "y") if k in node), default=0)


def kind_of(op: str) -> str:
    """The family of an operator.

    Args:
        op: Operator or terminal name.

    Returns:
        ``terminal``, ``unary``, ``binary``, ``ts``, ``ts2`` or ``cs``.

    Raises:
        ValueError: Unknown name.
    """
    for kind, names in (("unary", UNARY), ("binary", BINARY), ("ts", TS), ("ts2", TS2), ("cs", CS)):
        if op in names:
            return kind
    if op in TERMINALS:
        return "terminal"
    raise ValueError(f"unknown operator {op!r}")


def validate(node: Any, terminals: tuple[str, ...], panel: bool, max_depth: int = MAX_DEPTH,
             max_nodes: int = MAX_NODES) -> None:
    """Check an untrusted formula before it is evaluated.

    Args:
        node: The candidate tree, as parsed from JSON.
        terminals: Terminals the scope offers (``open, close, ret``... see ``TERMINALS``).
        panel: Whether cross-sectional operators are allowed (they need several instruments).
        max_depth: Deepest tree allowed.
        max_nodes: Most nodes allowed.

    Raises:
        ValueError: A node isn't a dict, has an unknown operator or terminal, missing or extra keys, a window that
            isn't a whole number between 1 (lags) or 2 (windows) and ``MAX_WINDOW``, or the tree is too big.
    """

    def check(n: Any, where: str) -> None:
        if not isinstance(n, dict) or not isinstance(n.get("op"), str):
            raise ValueError(f"{where}: each node must be an object with an 'op' string")
        op = n["op"]
        try:
            kind = kind_of(op)
        except ValueError:
            raise ValueError(f"{where}: unknown operator {op!r}") from None
        if kind == "terminal" and op not in terminals:
            raise ValueError(f"{where}: {op!r} isn't available here; use one of {', '.join(terminals)}")
        if kind == "cs" and not panel:
            raise ValueError(f"{where}: {op} compares instruments and needs a universe, not one instrument")
        need = {"terminal": {"op"}, "unary": {"op", "x"}, "binary": {"op", "x", "y"}, "ts": {"op", "x", "d"},
                "ts2": {"op", "x", "y", "d"}, "cs": {"op", "x"}}[kind]
        if set(n) != need:
            raise ValueError(f"{where}: {op} takes exactly the keys {sorted(need)}, got {sorted(n)}")
        if "d" in n:
            d = n["d"]
            low = 1 if op in LAG_OPS else 2
            whole = not isinstance(d, bool) and isinstance(d, int | float) and float(d) == int(d)
            if not whole or not low <= d <= MAX_WINDOW:
                raise ValueError(f"{where}: window of {op} must be a whole number from {low} to {MAX_WINDOW}")
        for k in ("x", "y"):
            if k in n:
                check(n[k], f"{where}.{k}")

    check(node, "formula")
    if depth(node) > max_depth:
        raise ValueError(f"formula: depth {depth(node)} is over the limit of {max_depth}")
    if node_count(node) > max_nodes:
        raise ValueError(f"formula: {node_count(node)} nodes is over the limit of {max_nodes}")
    if kind_of(node["op"]) == "terminal":
        raise ValueError("formula: a lone terminal isn't a signal; combine it with an operator")


def canonical(node: dict[str, Any]) -> dict[str, Any]:
    """The same formula with integer windows and keys in a fixed order, so equal formulas compare equal.

    Args:
        node: A validated tree.

    Returns:
        A new tree.
    """
    out: dict[str, Any] = {"op": node["op"]}
    for k in ("x", "y"):
        if k in node:
            out[k] = canonical(node[k])
    if "d" in node:
        out["d"] = int(node["d"])
    return out


def windows(node: dict[str, Any]) -> list[list[str]]:
    """Paths to every window in a formula, in pre-order (root first, then ``x`` before ``y``).

    Args:
        node: A tree.

    Returns:
        Key paths, e.g. ``[["d"], ["x", "d"]]``.
    """
    out: list[list[str]] = []

    def walk(n: dict[str, Any], path: list[str]) -> None:
        if "d" in n:
            out.append([*path, "d"])
        for k in ("x", "y"):
            if k in n:
                walk(n[k], [*path, k])

    walk(node, [])
    return out


def terminals_used(node: dict[str, Any]) -> list[str]:
    """Terminals a formula reads.

    Args:
        node: A tree.

    Returns:
        Sorted distinct terminal names.
    """
    if kind_of(node["op"]) == "terminal":
        return [node["op"]]
    return sorted({t for k in ("x", "y") if k in node for t in terminals_used(node[k])})


def ops_used(node: dict[str, Any]) -> list[str]:
    """Operators a formula applies (terminals left out).

    Args:
        node: A tree.

    Returns:
        Operator names in pre-order, repeats included.
    """
    own = [] if kind_of(node["op"]) == "terminal" else [node["op"]]
    return own + [o for k in ("x", "y") if k in node for o in ops_used(node[k])]


# ---------------------------------------------------------------------------------------------------------------
# Evaluation


def _clean(a: np.ndarray) -> np.ndarray:
    """Replace infinities with NaN.

    Args:
        a: Array.

    Returns:
        A float array.
    """
    a = np.asarray(a, dtype=float)
    return np.where(np.isfinite(a), a, np.nan)


def _roll(x: np.ndarray, d: int):
    """Trailing window of ``d`` rows that needs a value on every row.

    Args:
        x: ``(T, N)``.
        d: Window in rows.

    Returns:
        A pandas Rolling object over the columns.
    """
    return pd.DataFrame(x).rolling(d, min_periods=d)


def _shift(x: np.ndarray, d: int) -> np.ndarray:
    """``x`` from ``d`` rows earlier; the first ``d`` rows are NaN.

    Args:
        x: ``(T, N)``.
        d: Lag in rows, at least 1.

    Returns:
        ``(T, N)``.
    """
    out = np.full_like(x, np.nan)
    if d < len(x):
        out[d:] = x[:-d]
    return out


def _cs(x: np.ndarray, member: np.ndarray | None, fn: Callable[[pd.DataFrame], pd.DataFrame]) -> np.ndarray:
    """Apply a cross-sectional function row by row to the members of the universe.

    Args:
        x: ``(T, N)``.
        member: Point-in-time membership, or None for every instrument with a value.
        fn: Function of a DataFrame returning one of the same shape, computed along rows.

    Returns:
        ``(T, N)``; NaN outside the membership.
    """
    live = np.where(member, x, np.nan) if member is not None else x
    return _clean(fn(pd.DataFrame(live)).to_numpy())


def _apply(op: str, x: np.ndarray | None, y: np.ndarray | None, d: int, member: np.ndarray | None) -> np.ndarray:
    """One operator on evaluated children.

    Args:
        op: Operator name.
        x: First child, ``(T, N)``.
        y: Second child, ``(T, N)``, for binary operators and ``ts_corr``.
        d: Window for time-series operators.
        member: Membership mask for cross-sectional operators.

    Returns:
        ``(T, N)`` with NaN for undefined values.
    """
    with np.errstate(all="ignore"):
        if op == "neg":
            return -x
        if op == "abs":
            return np.abs(x)
        if op == "sign":
            return np.sign(x)
        if op == "slog":
            return np.sign(x) * np.log1p(np.abs(x))
        if op == "add":
            return x + y
        if op == "sub":
            return x - y
        if op == "mul":
            return x * y
        if op == "div":
            return np.where(np.abs(y) > EPS, x / np.where(np.abs(y) > EPS, y, 1.0), np.nan)
        if op == "delay":
            return _shift(x, d)
        if op == "delta":
            return x - _shift(x, d)
        if op == "ts_ret":
            past = _shift(x, d)
            return np.where(np.abs(past) > EPS, x / np.where(np.abs(past) > EPS, past, 1.0) - 1.0, np.nan)
        if op == "ts_mean":
            return _roll(x, d).mean().to_numpy()
        if op == "ts_sum":
            return _roll(x, d).sum().to_numpy()
        if op == "ts_std":
            return _roll(x, d).std().to_numpy()
        if op == "ts_min":
            return _roll(x, d).min().to_numpy()
        if op == "ts_max":
            return _roll(x, d).max().to_numpy()
        if op == "ts_rank":
            return _roll(x, d).rank(pct=True).to_numpy()
        if op == "ts_zscore":
            r = _roll(x, d)
            sd = r.std().to_numpy()
            return np.where(sd > EPS, (x - r.mean().to_numpy()) / np.where(sd > EPS, sd, 1.0), np.nan)
        if op == "ts_corr":
            return _roll(x, d).corr(pd.DataFrame(y)).to_numpy()
        if op == "cs_rank":
            return _cs(x, member, lambda f: f.rank(axis=1, pct=True))
        if op == "cs_zscore":
            return _cs(x, member, lambda f: f.sub(f.mean(axis=1), axis=0).div(f.std(axis=1), axis=0))
        if op == "cs_demean":
            return _cs(x, member, lambda f: f.sub(f.mean(axis=1), axis=0))
    raise ValueError(f"unknown operator {op!r}")


def evaluate(node: dict[str, Any], inputs: dict[str, np.ndarray], member: np.ndarray | None = None) -> np.ndarray:
    """Evaluate a validated formula on aligned data.

    Repeated subtrees are computed once.

    Args:
        node: A tree that passed ``validate``.
        inputs: Terminal name to ``(T, N)`` array.
        member: Point-in-time membership ``(T, N)`` for cross-sectional operators; None compares every instrument
            with a value.

    Returns:
        ``(T, N)`` signal; NaN where it is undefined.

    Raises:
        KeyError: The formula reads a terminal ``inputs`` doesn't have.
    """
    memo: dict[str, np.ndarray] = {}

    def ev(n: dict[str, Any]) -> np.ndarray:
        key = json.dumps(n, sort_keys=True)
        if key in memo:
            return memo[key]
        if kind_of(n["op"]) == "terminal":
            out = np.asarray(inputs[n["op"]], dtype=float)
        else:
            x = ev(n["x"]) if "x" in n else None
            y = ev(n["y"]) if "y" in n else None
            out = _clean(_apply(n["op"], x, y, int(n.get("d", 0)), member))
        memo[key] = out
        return out

    return ev(node)


# ---------------------------------------------------------------------------------------------------------------
# Plain English

_WORDS = {
    "delay": "{x} {d} days ago",
    "delta": "the {d}-day change in {x}",
    "ts_ret": "the {d}-day return of {x}",
    "ts_mean": "the {d}-day average of {x}",
    "ts_sum": "the {d}-day sum of {x}",
    "ts_std": "the {d}-day standard deviation of {x}",
    "ts_min": "the {d}-day low of {x}",
    "ts_max": "the {d}-day high of {x}",
    "ts_rank": "where {x} ranks among its last {d} days",
    "ts_zscore": "the {d}-day z-score of {x}",
    "ts_corr": "the {d}-day correlation of {x} with {y}",
    "neg": "minus {x}",
    "abs": "the size of {x}",
    "sign": "the sign of {x}",
    "slog": "the signed log of {x}",
    "add": "({x} plus {y})",
    "sub": "({x} minus {y})",
    "mul": "({x} times {y})",
    "div": "({x} divided by {y})",
    "cs_rank": "the rank across instruments of {x}",
    "cs_zscore": "the z-score across instruments of {x}",
    "cs_demean": "{x} relative to the average instrument",
}


def describe(node: dict[str, Any]) -> str:
    """A formula in plain English.

    Args:
        node: A tree.

    Returns:
        A phrase, e.g. "the 20-day z-score of the daily return".
    """
    op = node["op"]
    if kind_of(op) == "terminal":
        return TERMINALS[op]
    return _WORDS[op].format(x=describe(node["x"]), y=describe(node["y"]) if "y" in node else "",
                             d=int(node.get("d", 0)))


def to_text(node: dict[str, Any]) -> str:
    """A formula in compact functional notation, e.g. ``ts_zscore(ret, 20)``.

    Args:
        node: A tree.

    Returns:
        The expression.
    """
    op = node["op"]
    if kind_of(op) == "terminal":
        return op
    args = [to_text(node[k]) for k in ("x", "y") if k in node]
    if "d" in node:
        args.append(str(int(node["d"])))
    return f"{op}({', '.join(args)})"


# ---------------------------------------------------------------------------------------------------------------
# Sampling

def node_kinds(level: int, panel: bool) -> list[str]:
    """Node families a sampler may pick at a level of the tree.

    The list depends only on the level and the scope, so an Optuna study sees the same choices for the same
    decision in every trial (it refuses a categorical whose choices change).

    Args:
        level: Depth of the node (1 for the root).
        panel: Whether cross-sectional operators are allowed.

    Returns:
        Families among ``terminal, unary, binary, ts, ts2, cs``; the root can't be a terminal.
    """
    if level >= MAX_DEPTH:
        return ["terminal"]
    kinds = ["ts", "unary", "binary", "ts2"] + (["cs"] if panel else [])
    return kinds if level == 1 else ["terminal", *kinds]


def sample(chooser: Chooser, terminals: tuple[str, ...], panel: bool, name: str = "f", level: int = 1,
           budget: int = MAX_NODES) -> dict[str, Any]:
    """Draw a random formula, every decision through ``chooser``.

    Args:
        chooser: Source of choices; names are tree paths (``f``, ``f.x``, ``f.x.y``...).
        terminals: Terminals of the scope.
        panel: Whether cross-sectional operators are allowed.
        name: Path of this node.
        level: Depth of this node.
        budget: Nodes still allowed in this subtree.

    Returns:
        A formula tree that passes ``validate``.
    """
    kind = chooser.choice(f"{name}.kind", node_kinds(level, panel))
    if budget < 2 or (budget < 3 and kind in ("binary", "ts2")):
        kind = "terminal"  # no room left under MAX_NODES
    if kind == "terminal":
        return {"op": chooser.choice(f"{name}.t", list(terminals))}
    op = chooser.choice(f"{name}.{kind}", list({"unary": UNARY, "binary": BINARY, "ts": TS, "ts2": TS2,
                                                 "cs": CS}[kind]))
    node: dict[str, Any] = {"op": op}
    if kind in ("binary", "ts2"):
        node["x"] = sample(chooser, terminals, panel, f"{name}.x", level + 1, (budget - 1) // 2)
        node["y"] = sample(chooser, terminals, panel, f"{name}.y", level + 1, budget - 1 - node_count(node["x"]))
    else:
        node["x"] = sample(chooser, terminals, panel, f"{name}.x", level + 1, budget - 1)
    if kind in ("ts", "ts2"):
        node["d"] = int(chooser.choice(f"{name}.d", list(WINDOWS)))
    return node
