"""Search paradigms over the grammar, sharing one budgeted loop: random, Bayesian (TPE), genetic programming, LLM.

FactorBench (Wang & Ventre, 2026) found that no mining paradigm consistently dominates, so the lab runs several
and compares them on the same budget (``qlab.mining.calibration``). Every paradigm draws its first candidates from
the same distribution (``grammar.sample_spec``):

- **random**: independent draws from that distribution. The honest baseline.
- **tpe**: Optuna's tree-structured Parzen estimator over the same decisions (define-by-run, so the formula tree's
  choices are conditional parameters). It concentrates draws where earlier trials scored well.
- **gp**: genetic programming (Koza, 1992; Allen & Karjalainen, 1999): a population, tournament selection,
  subtree crossover and mutation of formulas, swaps and mutations of components, elitism, and a small parsimony
  pressure (complexity caps and penalties reduce overfitting; Becker & Seshadri, 2003).
- **llm**: proposals from a local language model, each with an economic rationale (``qlab.mining.llm``). The model
  never sees a result, so it proposes hypotheses rather than optimising the backtest.

Budgets are counted in distinct specs evaluated (each is a ledger trial) and in wall-clock seconds; a stop flag is
checked between batches. Repeats are never evaluated twice.
"""

from __future__ import annotations

import copy
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import formula, grammar
from .evaluate import INVALID, Result
from .grammar import RandomChooser, Space

PARADIGMS = ("random", "tpe", "gp", "llm")
PARSIMONY = 0.005  # edge Sharpe a GP candidate gives up per formula node when selected; ranking ignores it


@dataclass
class Budget:
    """How much a search may do.

    Attributes:
        trials: Distinct specs to evaluate.
        seconds: Wall-clock limit.
    """

    trials: int
    seconds: float = math.inf


@dataclass
class Outcome:
    """What a search did.

    Attributes:
        n_trials: Distinct specs evaluated.
        n_valid: Those that could be ranked.
        stopped: ``budget``, ``time``, ``stopped`` (asked to) or ``exhausted`` (no new specs found).
        elapsed: Seconds.
        best: The best result so far.
        curve: ``(trial number, best edge so far)`` after each batch.
    """

    n_trials: int = 0
    n_valid: int = 0
    stopped: str = "budget"
    elapsed: float = 0.0
    best: Result | None = None
    curve: list[tuple[int, float]] = field(default_factory=list)


def key(spec: dict[str, Any]) -> str:
    """Identity of a spec for de-duplication.

    Args:
        spec: A normalised spec.

    Returns:
        Canonical JSON.
    """
    return json.dumps(spec, sort_keys=True)


class Search:
    """The loop every paradigm runs in: propose a batch, evaluate it, record it, check the budget.

    Attributes:
        space: The grammar space.
        budget: Limits.
        seen: Specs evaluated, by ``key``, with their objective.
        outcome: Running totals.
    """

    def __init__(self, space: Space, evaluate: Callable[[list[dict[str, Any]]], list[Result]], budget: Budget,
                 on_batch: Callable[[list[Result]], None] | None = None,
                 should_stop: Callable[[], bool] | None = None) -> None:
        """Set up a search.

        Args:
            space: The grammar space.
            evaluate: Scores a batch of specs, in order.
            budget: Limits.
            on_batch: Called with each batch's results (records them in the ledger in a job).
            should_stop: Asked between batches; True ends the search.
        """
        self.space, self._evaluate, self.budget = space, evaluate, budget
        self.on_batch, self.should_stop = on_batch, should_stop or (lambda: False)
        self.seen: dict[str, float] = {}
        self.outcome = Outcome()
        self._t0 = time.monotonic()

    @property
    def left(self) -> int:
        """Trials left in the budget."""
        return max(0, self.budget.trials - self.outcome.n_trials)

    def done(self) -> bool:
        """Whether the search must end, setting the reason.

        Returns:
            True when the trial budget, the time limit or a stop request ends it.
        """
        if self.left <= 0:
            self.outcome.stopped = "budget"
            return True
        if time.monotonic() - self._t0 >= self.budget.seconds:
            self.outcome.stopped = "time"
            return True
        if self.should_stop():
            self.outcome.stopped = "stopped"
            return True
        return False

    def run_batch(self, specs: list[dict[str, Any]], limit: int | None = None) -> list[Result]:
        """Evaluate new specs (repeats and anything over the budget are dropped) and record them.

        Args:
            specs: Normalised specs.
            limit: Most specs to evaluate from this batch; None means all that fit the budget.

        Returns:
            Results of the specs actually evaluated, in order.
        """
        fresh, keys = [], set()
        for s in specs:
            k = key(s)
            if k in self.seen or k in keys:
                continue
            keys.add(k)
            fresh.append(s)
        fresh = fresh[: min(self.left, limit if limit is not None else self.left)]
        if not fresh:
            return []
        results = self._evaluate(fresh)
        o = self.outcome
        for r in results:
            self.seen[key(r.spec)] = r.edge
            o.n_trials += 1
            o.n_valid += r.ok
            if r.ok and (o.best is None or r.edge > o.best.edge):
                o.best = r
        o.curve.append((o.n_trials, o.best.edge if o.best else float("nan")))
        o.elapsed = time.monotonic() - self._t0
        if self.on_batch:
            self.on_batch(results)
        return results


# ---------------------------------------------------------------------------------------------------------------
# Random search


def random_search(search: Search, seed: int, batch: int) -> Outcome:
    """Independent draws from the grammar.

    Args:
        search: The loop.
        seed: Random seed.
        batch: Specs per batch.

    Returns:
        The outcome.
    """
    ch = RandomChooser(np.random.default_rng(seed))
    misses = 0
    while not search.done():
        specs = [grammar.sample_spec(ch, search.space) for _ in range(2 * min(batch, search.left))]
        got = search.run_batch(specs, limit=batch)
        misses = misses + 1 if not got else 0
        if misses > 20:
            search.outcome.stopped = "exhausted"
            break
    return search.outcome


# ---------------------------------------------------------------------------------------------------------------
# Bayesian search (Optuna TPE)


class OptunaChooser:
    """A chooser backed by an Optuna trial (define-by-run): each decision is a categorical parameter.

    Attributes:
        trial: The Optuna trial.
    """

    def __init__(self, trial: Any) -> None:
        """Wrap a trial.

        Args:
            trial: An ``optuna.trial.Trial`` from ``study.ask()``.
        """
        self.trial = trial

    def choice(self, name: str, options: list[Any]) -> Any:
        """Ask the sampler for one of the options.

        Args:
            name: Parameter name (unique per decision).
            options: Choices; must be the same list every time the name is used in a study.

        Returns:
            The pick.
        """
        return self.trial.suggest_categorical(name, list(options))


def tpe_search(search: Search, seed: int, batch: int) -> Outcome:
    """Tree-structured Parzen estimator over the grammar's decisions.

    The first trials are random draws (``n_startup_trials``); later ones favour decisions that appeared in
    high-scoring specs. A batch asks for several trials at once with the constant-liar heuristic, so workers stay
    busy. A repeat is told its earlier score and doesn't use the budget.

    Args:
        search: The loop.
        seed: Random seed.
        batch: Trials per batch.

    Returns:
        The outcome.
    """
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    startup = max(20, min(100, search.budget.trials // 10))
    sampler = optuna.samplers.TPESampler(seed=seed, n_startup_trials=startup, constant_liar=batch > 1)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    repeats = 0
    while not search.done():
        asked, specs = [], []
        while len(specs) < min(batch, search.left) and repeats < 2000:
            trial = study.ask()
            spec = grammar.sample_spec(OptunaChooser(trial), search.space)
            k = key(spec)
            if k in search.seen:
                study.tell(trial, search.seen[k])
                repeats += 1
                continue
            if any(key(s) == k for s in specs):
                study.tell(trial, state=optuna.trial.TrialState.FAIL)
                repeats += 1
                continue
            asked.append(trial)
            specs.append(spec)
        if not specs:
            search.outcome.stopped = "exhausted"
            break
        results = search.run_batch(specs)
        by_key = {key(r.spec): r.edge for r in results}
        for trial, spec in zip(asked, specs, strict=True):
            value = by_key.get(key(spec))
            if value is None:
                study.tell(trial, state=optuna.trial.TrialState.FAIL)
            else:
                study.tell(trial, value if math.isfinite(value) else INVALID)
    return search.outcome


# ---------------------------------------------------------------------------------------------------------------
# Genetic programming


def _paths(node: dict[str, Any], path: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Every node's key path in a formula, root first.

    Args:
        node: A tree.
        path: Path of ``node``.

    Returns:
        Key paths.
    """
    out = [path]
    for k in ("x", "y"):
        if k in node:
            out += _paths(node[k], (*path, k))
    return out


def _at(node: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    """The subtree at a path.

    Args:
        node: A tree.
        path: Keys.

    Returns:
        The subtree.
    """
    for k in path:
        node = node[k]
    return node


def _put(node: dict[str, Any], path: tuple[str, ...], sub: dict[str, Any]) -> dict[str, Any]:
    """A copy of a tree with the subtree at ``path`` replaced.

    Args:
        node: A tree.
        path: Keys; empty replaces the root.
        sub: New subtree.

    Returns:
        The new tree.
    """
    if not path:
        return copy.deepcopy(sub)
    out = copy.deepcopy(node)
    parent = _at(out, path[:-1])
    parent[path[-1]] = copy.deepcopy(sub)
    return out


class Genetic:
    """Variation operators of the genetic programme, on normalised specs.

    Attributes:
        space: The grammar space.
        rng: Random generator.
    """

    def __init__(self, space: Space, rng: np.random.Generator) -> None:
        """Set up the operators.

        Args:
            space: The grammar space.
            rng: Random generator.
        """
        self.space, self.rng, self.ch = space, rng, RandomChooser(rng)

    def fresh(self) -> dict[str, Any]:
        """A new random spec (an immigrant).

        Returns:
            A spec.
        """
        return grammar.sample_spec(self.ch, self.space)

    def _pick(self, items: list[Any]) -> Any:
        """One item at random.

        Args:
            items: Choices.

        Returns:
            The pick.
        """
        return items[int(self.rng.integers(len(items)))]

    def crossover(self, a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        """A child of two specs: a subtree swap when both have formulas, otherwise one component from ``b``.

        Args:
            a: First parent (the child starts as a copy of it).
            b: Second parent.

        Returns:
            A spec, not yet validated.
        """
        child = copy.deepcopy(a)
        fa, fb = a["signal"].get("formula"), b["signal"].get("formula")
        if fa is not None and fb is not None and self.rng.random() < 0.7:
            pa = self._pick(_paths(fa))
            pb = self._pick(_paths(fb))
            if not pa and formula.kind_of(_at(fb, pb)["op"]) == "terminal":
                pb = ()
            child["signal"]["formula"] = _put(fa, pa, _at(fb, pb))
            return child
        parts = [k for k in ("signal", "filter", "exit", "session", "sizing", "direction", "hold")
                 if k in a and k in b]
        part = self._pick(parts)
        child[part] = copy.deepcopy(b[part])
        if part == "signal":  # direction and hold belong to formulas on their own space
            for k in ("direction", "hold"):
                child.pop(k, None)
                if k in b:
                    child[k] = copy.deepcopy(b[k])
        return child

    def mutate(self, a: dict[str, Any]) -> dict[str, Any]:
        """A random change to one part of a spec.

        Formula: change an operator for another of its kind, a terminal for another, a window to a neighbouring
        rung of the ladder, or a subtree for a fresh one. Rule: move one parameter one grid step. Components: draw
        one again.

        Args:
            a: The parent.

        Returns:
            A spec, not yet validated.
        """
        child = copy.deepcopy(a)
        node = a["signal"].get("formula")
        moves = ["component"] * 2 + (["op", "window", "subtree"] * 2 if node is not None else ["param"] * 3)
        move = self._pick(moves)
        if move == "component":
            other = self.fresh()
            parts = [k for k in ("filter", "exit", "session", "sizing", "direction", "hold") if k in a and k in other]
            if parts:
                part = self._pick(parts)
                child[part] = other[part]
            return child
        if move == "param":
            rule = grammar.rule_of(self.space, a["signal"]["rule"])
            name = self._pick(list(rule.params))
            grid = grammar._grid(rule.params[name])
            cur = a["signal"]["params"][name]
            i = int(np.argmin([abs(g - cur) for g in grid]))
            j = int(np.clip(i + self._pick([-1, 1]), 0, len(grid) - 1))
            child["signal"]["params"][name] = grid[j]
            return child
        if move == "window":
            spots = formula.windows(node)
            if spots:
                path = self._pick(spots)
                sub = _at(node, tuple(path[:-1]))
                ladder = list(formula.WINDOWS)
                i = int(np.argmin([abs(w - sub["d"]) for w in ladder]))
                new = copy.deepcopy(node)
                _at(new, tuple(path[:-1]))["d"] = ladder[int(np.clip(i + self._pick([-1, 1]), 0, len(ladder) - 1))]
                child["signal"]["formula"] = new
                return child
            move = "op"
        path = self._pick(_paths(node))
        sub = _at(node, path)
        if move == "op":
            kind = formula.kind_of(sub["op"])
            pool = {"terminal": self.space.open_terminals, "unary": formula.UNARY, "binary": formula.BINARY,
                    "ts": formula.TS, "ts2": formula.TS2, "cs": formula.CS}[kind]
            pool = [o for o in pool if self.space.allowed(o)]
            new_sub = {**sub, "op": self._pick(pool)}
            if new_sub["op"] in formula.LAG_OPS or "d" not in new_sub:
                pass
            elif new_sub["d"] < 2:
                new_sub["d"] = 2
            child["signal"]["formula"] = _put(node, path, new_sub)
            return child
        room = formula.MAX_NODES - formula.node_count(node) + formula.node_count(sub)
        new_sub = formula.sample(grammar._Banned(self.ch, self.space), self.space.open_terminals,
                                 self.space.kind == "panel", "g", len(path) + 1, room)
        if not path and formula.kind_of(new_sub["op"]) == "terminal":
            return child
        child["signal"]["formula"] = _put(node, path, new_sub)
        return child

    def valid(self, spec: dict[str, Any]) -> dict[str, Any] | None:
        """The spec in canonical form if it fits the grammar.

        Args:
            spec: A candidate.

        Returns:
            The normalised spec, or None.
        """
        try:
            return grammar.validate_spec(spec, self.space)
        except (ValueError, KeyError, TypeError):
            return None


def _tournament(pop: list[tuple[dict[str, Any], float]], rng: np.random.Generator, size: int) -> dict[str, Any]:
    """The fittest of a few individuals drawn at random.

    Args:
        pop: ``(spec, fitness)`` pairs.
        rng: Random generator.
        size: Contestants.

    Returns:
        The winner's spec.
    """
    idx = rng.integers(len(pop), size=min(size, len(pop)))
    return pop[int(max(idx, key=lambda i: pop[i][1]))][0]


def gp_search(search: Search, seed: int, batch: int, population: int | None = None, elite: int = 2,
              tournament: int = 3, p_crossover: float = 0.6) -> Outcome:
    """Genetic programming over specs.

    Fitness is the edge Sharpe after costs minus ``PARSIMONY`` per formula node; the job ranks by the plain edge.

    Args:
        search: The loop.
        seed: Random seed.
        batch: Unused beyond sizing the first generation; each generation is one batch.
        population: Individuals per generation; None picks ``clip(budget / 15, 10, 60)``.
        elite: Best individuals copied into the next generation unchanged.
        tournament: Contestants per selection.
        p_crossover: Share of children made by crossover (the rest by mutation).

    Returns:
        The outcome.
    """
    rng = np.random.default_rng(seed)
    ops = Genetic(search.space, rng)
    size = population or int(np.clip(search.budget.trials // 15, 10, 60))

    def fitness(r: Result) -> float:
        return r.edge - PARSIMONY * float(r.metrics.get("nodes", 1)) if r.ok else INVALID

    pop: list[tuple[dict[str, Any], float]] = []
    tries = 0
    while len(pop) < size and not search.done() and tries < 50:
        tries += 1
        got = search.run_batch([ops.fresh() for _ in range(size - len(pop))])
        pop += [(r.spec, fitness(r)) for r in got]
    while not search.done():
        pop.sort(key=lambda x: -x[1])
        nxt = pop[:elite]
        children, attempts = [], 0
        want = min(size - len(nxt), search.left)
        while len(children) < want and attempts < 30 * size:
            attempts += 1
            if rng.random() < p_crossover:
                child = ops.crossover(_tournament(pop, rng, tournament), _tournament(pop, rng, tournament))
            else:
                child = ops.mutate(_tournament(pop, rng, tournament))
            child = ops.valid(child) or (ops.valid(ops.fresh()) if attempts % 10 == 0 else None)
            if child is None:
                continue
            k = key(child)
            if k in search.seen or any(key(c) == k for c in children):
                continue
            children.append(child)
        if not children:
            search.outcome.stopped = "exhausted"
            break
        got = search.run_batch(children)
        pop = nxt + [(r.spec, fitness(r)) for r in got]
    return search.outcome


def run(paradigm: str, search: Search, seed: int, batch: int = 16, proposer: Any = None) -> Outcome:
    """Run one paradigm in a search loop.

    Args:
        paradigm: One of ``PARADIGMS``.
        search: The loop.
        seed: Random seed.
        batch: Specs per batch (workers stay busy with at least one per worker).
        proposer: For ``llm``: an object with ``propose(space, n) -> list[(spec, rationale)]``.

    Returns:
        The outcome.

    Raises:
        ValueError: Unknown paradigm, or ``llm`` without a proposer.
    """
    if paradigm == "random":
        return random_search(search, seed, batch)
    if paradigm == "tpe":
        return tpe_search(search, seed, batch)
    if paradigm == "gp":
        return gp_search(search, seed, batch)
    if paradigm == "llm":
        if proposer is None:
            raise ValueError("the llm paradigm needs a proposer")
        return llm_search(search, proposer, batch)
    raise ValueError(f"unknown paradigm {paradigm!r}; use one of {', '.join(PARADIGMS)}")


def llm_search(search: Search, proposer: Any, batch: int) -> Outcome:
    """Evaluate a language model's proposals until the budget ends or it stops proposing new specs.

    Args:
        search: The loop.
        proposer: Has ``propose(space, n) -> list[(spec, rationale)]``; specs are validated already.
        batch: Proposals asked for per round.

    Returns:
        The outcome.
    """
    empty = 0
    while not search.done():
        props = proposer.propose(search.space, min(batch, search.left))
        got = search.run_batch([s for s, _ in props])
        empty = empty + 1 if not got else 0
        if empty >= 3:
            search.outcome.stopped = "exhausted"
            break
    return search.outcome
