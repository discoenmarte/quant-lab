"""Tests for M5: the formula language, the grammar, sessions in the engine, scoring, the search paradigms, budgeted
jobs and the ledger, the gauntlet hand-off, the language-model proposer, mining calibration and the API."""

import copy

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from qlab import audit, ledger, lockbox
from qlab.api.main import create_app
from qlab.backtest.engine import COST_PRESETS, run
from qlab.backtest.leakage import future_shift_check
from qlab.data import store
from qlab.data.bars import normalize_bars
from qlab.gauntlet import calibration as gcal
from qlab.gauntlet import thresholds
from qlab.gauntlet.subjects import subject_from_meta
from qlab.inbox.llm import Answer
from qlab.mining import calibration as mcal
from qlab.mining import formula, grammar, jobs, llm
from qlab.mining import search as S
from qlab.mining.evaluate import INVALID, Evaluator
from qlab.paper import book
from qlab.portfolio import leakage as panel_leakage
from qlab.portfolio.engine import run_portfolio
from qlab.portfolio.synthetic import synthetic_crypto_panel, synthetic_panel

FAST = {"n_perm": 60, "n_boot": 40, "grid_max": 30}


def bars_from(log_returns: np.ndarray, start: str = "2008-01-02") -> pd.DataFrame:
    """Daily bars whose closes follow the given log returns, with opens, ranges and volume around them."""
    rng = np.random.default_rng(0)
    n = len(log_returns)
    close = 100 * np.exp(np.cumsum(log_returns))
    open_ = close * np.exp(rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.004, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.004, n))
    ts = pd.bdate_range(start, periods=n, tz="UTC")
    return normalize_bars(pd.DataFrame({"ts": ts, "open": open_, "high": high, "low": low, "close": close,
                                        "volume": rng.lognormal(14, 0.3, n)}))


@pytest.fixture
def spy():
    """Seventeen years of synthetic SPY bars in the store, through the lockbox date."""
    store.write_series("yahoo", "SPY", "1d", bars_from(np.random.default_rng(3).normal(0.0003, 0.011, 4400)))
    return jobs.make_scope("bars", "SPY", "yahoo")


@pytest.fixture
def bars_ev():
    """An evaluator on twelve years of synthetic bars."""
    return Evaluator.synthetic(bars=gcal.synthetic_bars(12, 0.0, 5))


# ---------------------------------------------------------------------------------------------------------------
# Formula language


def test_formula_validation_rejects_anything_outside_the_whitelist():
    """Unknown operators and terminals, extra keys, bad windows, cross-sectional operators on one instrument, lone
    terminals and oversized trees are refused before anything runs."""
    terms = ("close", "ret")
    good = {"op": "ts_mean", "x": {"op": "ret"}, "d": 20}
    formula.validate(good, terms, panel=False)
    bad = [
        ({"op": "eval", "x": {"op": "ret"}}, "unknown operator"),
        ({"op": "ts_mean", "x": {"op": "volume"}, "d": 20}, "isn't available"),
        ({"op": "ts_mean", "x": {"op": "ret"}, "d": 20, "code": "import os"}, "exactly the keys"),
        ({"op": "ts_mean", "x": {"op": "ret"}, "d": 0}, "window"),
        ({"op": "ts_std", "x": {"op": "ret"}, "d": 1}, "window"),
        ({"op": "ts_mean", "x": {"op": "ret"}, "d": 2.5}, "window"),
        ({"op": "cs_rank", "x": {"op": "ret"}}, "needs a universe"),
        ({"op": "close"}, "lone terminal"),
        ("ts_mean(ret, 20)", "object"),
    ]
    for node, msg in bad:
        with pytest.raises(ValueError, match=msg):
            formula.validate(node, terms, panel=False)
    deep = {"op": "ret"}
    for _ in range(formula.MAX_DEPTH):
        deep = {"op": "neg", "x": deep}
    with pytest.raises(ValueError, match="depth"):
        formula.validate(deep, terms, panel=False)
    formula.validate({"op": "delay", "x": {"op": "close"}, "d": 1}, terms, panel=False)  # lags may use one day


def test_formulas_only_read_the_past():
    """Changing rows after a cut never changes a formula's values up to it, for random formulas of every family,
    cross-sectional operators included."""
    rng = np.random.default_rng(1)
    t, n = 400, 12
    base = {k: rng.lognormal(0, 0.3, (t, n)) for k in ("open", "close", "dvol")}
    base["ret"] = rng.normal(0, 0.02, (t, n))
    base["mkt"] = np.repeat(base["ret"].mean(axis=1, keepdims=True), n, axis=1)
    member = rng.random((t, n)) < 0.8
    ch = grammar.RandomChooser(rng)
    terms = tuple(base)
    for _ in range(150):
        node = formula.sample(ch, terms, panel=True)
        formula.validate(node, terms, panel=True)
        before = formula.evaluate(node, base, member)
        cut = int(rng.integers(50, t - 2))
        shocked = {k: v.copy() for k, v in base.items()}
        for v in shocked.values():
            v[cut + 1:] *= rng.uniform(0.5, 1.5, v[cut + 1:].shape)
        again = formula.evaluate(node, shocked, member)
        assert np.allclose(before[: cut + 1], again[: cut + 1], equal_nan=True), formula.to_text(node)


def test_formula_text_and_words():
    """A formula reads as an expression and in plain English."""
    node = {"op": "neg", "x": {"op": "ts_zscore", "x": {"op": "ret"}, "d": 5}}
    assert formula.to_text(node) == "neg(ts_zscore(ret, 5))"
    assert formula.describe(node) == "minus the 5-day z-score of the daily return"
    assert formula.windows(node) == [["x", "d"]]
    assert formula.ops_used(node) == ["neg", "ts_zscore"] and formula.terminals_used(node) == ["ret"]


# ---------------------------------------------------------------------------------------------------------------
# Grammar


def test_sampled_specs_validate_compile_and_pass_the_leakage_checks(bars_ev):
    """Every sampled spec round-trips through validation, compiles, holds positions in [-1, 1] and passes the
    look-ahead check, on one instrument and on a crypto cross-section."""
    ch = grammar.RandomChooser(np.random.default_rng(2))
    bars = bars_ev.bars
    for i in range(40):
        spec = grammar.sample_spec(ch, bars_ev.space)
        assert grammar.validate_spec(copy.deepcopy(spec), bars_ev.space) == spec
        strat = grammar.compile_spec(spec, bars_ev.space)
        pos = strat.positions(bars)
        assert pos.between(-1, 1).all()
        assert future_shift_check(strat, bars, n_cuts=2, seed=i)["passed"], grammar.describe(spec)
        assert len(strat.params) <= grammar.MAX_TUNABLE
    panel = synthetic_crypto_panel(3.0, 60, 20, seed=4)
    space = grammar.panel_space(panel)
    assert "dvol" in space.terminals and "gap" not in space.terminals and space.rules == grammar.DYNAMIC_RULES
    for i in range(12):
        spec = grammar.sample_spec(ch, space)
        strat = grammar.compile_spec(spec, space)
        w = strat.weights(panel)
        assert np.all(np.abs(w).sum(axis=1) <= 1.0 + 1e-9)
        assert panel_leakage.future_shift_check(strat, panel, n_cuts=2, seed=i)["passed"], grammar.describe(spec)


def test_untrusted_specs_are_refused_with_reasons():
    """What a language model might get wrong is refused with a message, and bans hold."""
    space = grammar.bars_space("equity", 252, banned=("ts_corr", "overnight"))
    ok = {"signal": {"formula": {"op": "ts_mean", "x": {"op": "ret"}, "d": 20}}}
    assert grammar.validate_spec(ok, space)["direction"] == "long"
    bad = [
        ({"signal": {"rule": "trend_sma", "params": {"window": 9999}}}, "between"),
        ({"signal": {"rule": "xs_reversal"}}, "signal.rule"),
        ({"signal": ok["signal"], "filter": {"kind": "moon", "window": 5}}, "filter.kind"),
        ({"signal": ok["signal"], "session": "overnight"}, "session"),
        ({"signal": ok["signal"], "hold": {"quantile": 0.2, "rebalance": 5}}, "unknown keys"),
        ({"signal": {"formula": {"op": "ts_corr", "x": {"op": "ret"}, "y": {"op": "close"}, "d": 20}}}, "banned"),
        ({"signal": ok["signal"], "exit": {"kind": "time"}}, "exit"),
        ({"signal": {"rule": "trend_sma", "formula": ok["signal"]["formula"]}}, "exactly one"),
        ([], "object"),
    ]
    for spec, msg in bad:
        with pytest.raises(ValueError, match=msg):
            grammar.validate_spec(spec, space)
    crypto = grammar.bars_space("crypto", 365)
    with pytest.raises(ValueError, match="never closes"):
        grammar.validate_spec({**ok, "session": "overnight"}, crypto)


def test_names_and_parameters_split_structure_from_numbers():
    """Specs that differ only in their tunable numbers share a strategy name; the numbers are its parameters, and
    the strategy rebuilds from storage."""
    space = grammar.bars_space("equity", 252)
    a = grammar.normalize({"signal": {"formula": {"op": "ts_mean", "x": {"op": "ret"}, "d": 20}},
                           "filter": {"kind": "trend", "window": 200}}, space)
    b = copy.deepcopy(a)
    b["signal"]["formula"]["d"] = 40
    name_a, _, params_a = grammar.split(a, space)
    name_b, _, params_b = grammar.split(b, space)
    assert name_a == name_b and name_a.startswith("mined_")
    assert params_a == {"w1": 20, "filter_window": 200} and params_b["w1"] == 40
    strat = grammar.compile_spec(a, space)
    assert strat.params["w1"].step == 4 and strat.params["w1"].low == 12 and strat.params["w1"].high == 28
    grammar.save_specs([(a, space)], "job")
    again = grammar.load_strategy(name_a)
    assert again.spec == a and again.family == "mined"


def test_session_legs_in_the_engine():
    """Overnight-only earns the gaps of the position decided two closes before; day-only the bodies of the one
    decided the close before; both pay a round trip every day they hold."""
    bars = gcal.synthetic_bars(2, 0.0, 9)
    target = pd.Series(1.0, index=bars.index)
    model = COST_PRESETS["etf"]
    night = run(bars, target, model, with_trades=False, session="overnight")
    day = run(bars, target, model, with_trades=False, session="day")
    full = run(bars, target, model, with_trades=False)
    c, o = bars["close"].to_numpy(), bars["open"].to_numpy()
    gap = o[3:] / c[2:-1] - 1
    body = c[3:] / o[3:] - 1
    assert np.allclose(night.gross.to_numpy()[3:], gap)
    assert np.allclose(day.gross.to_numpy()[3:], body)
    assert np.allclose(night.costs.to_numpy()[3:], 2 * model.per_unit)
    assert full.costs.to_numpy()[3:].sum() == 0
    with pytest.raises(ValueError, match="session"):
        run(bars, target, model, session="lunch")


# ---------------------------------------------------------------------------------------------------------------
# Scoring and search


def test_evaluator_scores_specs_and_flags_leaks(bars_ev, monkeypatch):
    """A spec gets the ledger's numbers; one that peeks at the future is flagged and never ranked."""
    spec = grammar.normalize({"signal": {"rule": "trend_sma", "params": {}}}, bars_ev.space)
    r = bars_ev.evaluate(spec)
    assert r.ok and np.isfinite(r.edge) and r.metrics["edge_sharpe"] == pytest.approx(r.edge)
    assert {"gross_edge_sharpe", "turnover", "nodes", "leak"} <= set(r.metrics)
    real = grammar.bar_positions

    def peek(spec, bars, dpy):
        return real(spec, bars, dpy).shift(-1).fillna(0.0)

    monkeypatch.setattr(grammar, "bar_positions", peek)
    fast = grammar.normalize({"signal": {"formula": {"op": "delta", "x": {"op": "close"}, "d": 1}},
                              "direction": "long_short"}, bars_ev.space)
    leaky = bars_ev.evaluate(fast)
    assert leaky.leak and not leaky.ok and leaky.edge == INVALID


@pytest.mark.parametrize("paradigm", ["random", "tpe", "gp"])
def test_paradigms_spend_exactly_their_budget_without_repeats(bars_ev, paradigm):
    """Each paradigm evaluates its budget of distinct specs and tracks the best."""
    seen = []
    srch = S.Search(bars_ev.space, lambda specs: [bars_ev.evaluate(s, keep_daily=False) for s in specs],
                    S.Budget(60), on_batch=seen.extend)
    out = S.run(paradigm, srch, seed=4, batch=8)
    assert out.n_trials == 60 and out.stopped == "budget"
    keys = [S.key(r.spec) for r in seen]
    assert len(keys) == len(set(keys)) == 60
    assert out.best is not None and out.best.edge == max(r.edge for r in seen if r.ok)
    assert out.curve[-1][0] == 60


def test_searches_stop_on_request_and_on_time(bars_ev):
    """A stop request and the time limit end a search between batches."""
    calls = []
    srch = S.Search(bars_ev.space, lambda specs: [bars_ev.evaluate(s, keep_daily=False) for s in specs],
                    S.Budget(1000), on_batch=calls.append, should_stop=lambda: len(calls) >= 2)
    out = S.random_search(srch, 1, 5)
    assert out.stopped == "stopped" and out.n_trials == 10
    srch = S.Search(bars_ev.space, lambda specs: [bars_ev.evaluate(s, keep_daily=False) for s in specs],
                    S.Budget(1000, seconds=0.0))
    assert S.gp_search(srch, 1, 5).stopped == "time"


# ---------------------------------------------------------------------------------------------------------------
# Jobs, budgets, the ledger and the gauntlet


def test_a_job_records_every_trial_on_research_data_and_hands_its_best_to_the_gauntlet(spy, monkeypatch):
    """Every evaluated spec is a mining trial on mined:SPY:1d inside the research period; the report has the
    race against the luck threshold, coverage and originality; the best goes to the gauntlet with N counting the
    job; the card rebuilds from its meta."""
    thresholds.update(FAST, "fast tests")
    row = jobs.create(spy, "gp", 80, 5, seed=2, workers=1, note="test")
    rep = jobs.run(row["job_id"], log=lambda m: None)
    assert rep["status"] == "done" and rep["progress"]["n_trials"] == 80
    trials = ledger.list_trials(1000, question="mined:SPY:1d")
    assert len(trials) == 80 and {t["source"] for t in trials} == {"mining"}
    assert max(t["end_date"] for t in trials) < str(lockbox.sealed_from().date())
    assert {r["rule"] for r in rep["reference"]} == set(grammar.BAR_RULES)
    assert rep["curve"][-1]["N"] == rep["progress"]["N"] and rep["curve"][-1]["sr0"] > 0
    assert rep["top"] and rep["top"][0]["edge"] >= rep["top"][-1]["edge"]
    assert 0 <= rep["top"][0]["dsr"] <= 1 and "originality" in rep["top"][0]
    assert sum(rep["coverage"]["signals"].values()) == 80 and sum(rep["histogram"]["counts"]) > 0
    assert jobs.budget_of("mined:SPY:1d")["used"] == 80

    best = rep["top"][0]
    card = jobs.send_to_gauntlet(row["job_id"], best["strategy"])
    m = card["meta"]
    assert m["question"] == "mined:SPY:1d" and m["family"] == "mined" and m["mined"]["job"] == row["job_id"]
    assert m["n_trials"] >= 70  # every scored mining trial on SPY counts, the job's references included
    assert jobs.load(row["job_id"])["cards"][0]["card_id"] == card["card_id"]
    keys = ("kind", "source", "symbol", "universe", "interval", "strategy", "costs", "lockbox_from")
    subject = subject_from_meta({k: m.get(k) for k in keys}, lockbox.sealed_from())
    assert subject.strategy == best["strategy"] and subject.question == "mined:SPY:1d"
    assert any(e["action"] == "mining.gauntlet" for e in audit.recent())


def test_budgets_cap_each_question_and_need_a_reason(spy):
    """A job takes at most what is left of its question's budget; changing a budget needs a reason."""
    with pytest.raises(ValueError, match="reason"):
        jobs.set_budget("mined:SPY:1d", 30, "")
    with pytest.raises(ValueError, match="mining question"):
        jobs.set_budget("trend_sma:SPY:1d", 30, "x")
    jobs.set_budget("mined:SPY:1d", 25, "small test budget")
    rep = jobs.run(jobs.create(spy, "random", 100, 5, workers=1)["job_id"], log=lambda m: None)
    assert rep["progress"]["n_trials"] == 25
    rep = jobs.run(jobs.create(spy, "random", 100, 5, workers=1)["job_id"], log=lambda m: None)
    assert rep["status"] == "refused" and "budget is used" in rep["message"]
    assert jobs.budget_of("mined:SPY:1d")["remaining"] == 0


def test_a_job_runs_on_worker_processes_without_opening_the_database(spy, monkeypatch):
    """With several workers, specs are scored in other processes, which never read the lab database."""
    rep = jobs.run(jobs.create(spy, "random", 24, 5, workers=2)["job_id"], log=lambda m: None)
    assert rep["status"] == "done" and rep["progress"]["n_trials"] == 24


def test_session_rules_stay_out_of_the_paper_book(spy):
    """A mined rule held only overnight can be judged, but the book refuses it: it would need orders at the close."""
    thresholds.update(FAST, "fast tests")
    space = grammar.bars_space("equity", 252)
    spec = grammar.normalize({"signal": {"rule": "trend_sma", "params": {}}, "session": "overnight"}, space)
    sc = jobs.parse_scope("SPY")
    row = jobs.create(sc, "random", 10, 1, workers=1)
    rep = jobs.load(row["job_id"])
    rep.update(space=space.to_dict(), top=[{"strategy": grammar.split(spec, space)[0], "spec": spec, "text": "x",
                                            "params": grammar.split(spec, space)[2]}])
    jobs._save(row["job_id"], rep)
    grammar.save_specs([(spec, space)])
    card = jobs.send_to_gauntlet(row["job_id"], rep["top"][0]["strategy"])
    with pytest.raises(book.Refused, match="overnight"):
        book.add(card["card_id"])


# ---------------------------------------------------------------------------------------------------------------
# The language-model proposer


class FakeLLM:
    """A provider that answers from a script, recording the prompts it saw."""

    name, model = "fake", "fake-1"

    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    def available(self):
        return True

    def ask_json(self, system, prompt, schema=None):
        self.prompts.append(prompt)
        return Answer(data=self.answers.pop(0), provider="fake", model="fake-1", seconds=0.1)


def test_proposals_are_validated_in_the_loop_and_nothing_else_runs():
    """Valid proposals with a rationale are kept; invalid ones go back with reasons once; ideas outside the grammar
    are kept as notes; code in an answer is never executed."""
    space = grammar.bars_space("equity", 252)
    why = "Investors underreact to slow news, so recent drift continues for weeks; low turnover keeps costs small."
    good = {"rationale": why, "spec": {"signal": {"formula": {"op": "ts_mean", "x": {"op": "ret"}, "d": 60}}}}
    first = {"proposals": [good, {"rationale": "short", "spec": good["spec"]},
                           {"rationale": why, "spec": {"signal": {"formula": {"op": "__import__"}}}}],
             "outside": ["Use insider filings"]}
    fixed = {"proposals": [{"rationale": why, "spec": {"signal": {"rule": "tsmom", "params": {"lookback": 126}}}}]}
    fake = FakeLLM([first, fixed])
    prop = llm.Proposer("SPY", llm=fake)
    got = prop.propose(space, 3)
    assert len(got) == 2 and prop.rejected == 2 and prop.calls == 2
    assert "invalid proposals" in fake.prompts[1] and "rationale must be" in fake.prompts[1]
    assert prop.outside == ["Use insider filings"]
    assert all(a["rationale"].startswith("Investors") for a in prop.accepted)
    assert prop.rationale_of(got[0][0]) == why


def test_the_llm_paradigm_runs_a_job_with_rationales(spy):
    """A job with the llm paradigm evaluates validated proposals and keeps each one's rationale."""
    why = "Momentum: investors underreact to information, so trends persist over months before reverting."
    answers = [{"proposals": [{"rationale": why, "spec": {"signal": {"formula": {
        "op": "ts_ret", "x": {"op": "close"}, "d": d}}}} for d in (60, 120, 250)]}] + [{"proposals": []}] * 10
    prop = llm.Proposer("SPY", llm=FakeLLM(answers))
    rep = jobs.run(jobs.create(spy, "llm", 30, 5, workers=1)["job_id"], log=lambda m: None, proposer=prop)
    assert rep["status"] == "done" and rep["stopped"] == "exhausted"
    assert len(rep["proposals"]) == 3 and rep["llm"]["calls"] >= 2
    assert all(p["rationale"] == why for p in rep["proposals"])
    recorded = {t["strategy"] for t in ledger.list_trials(1000, question="mined:SPY:1d")}
    assert {p["strategy"] for p in rep["proposals"]} <= recorded


# ---------------------------------------------------------------------------------------------------------------
# Calibration at mining scale


def test_miner_researcher_runs_the_unchanged_gauntlet():
    """A miner searches a synthetic market, the gauntlet judges its best deflated against every trial, and the run
    keeps what it needs to be re-judged at a larger N."""
    _, th = thresholds.current()
    th = {**th, **FAST}
    task = {"scenario": "noise_mined", "family": "mine/gp/bars", "market": "bars", "paradigm": "gp", "years": 8,
            "trend": 0.0, "seed": 31, "budget": 40, "th": th}
    r = mcal.mine_one(task)
    assert r["mined"]["n_trials"] == 40 and isinstance(r["admitted"], bool)
    assert r["dsr_inputs"]["n_trials"] == 2 * r["mined"]["n_valid"]  # mined trials count twice (thresholds v5)
    assert len(r["miner_records"]) == 40
    rep = {"thresholds": th, "runs": [r]}
    loose = mcal.rejudge(rep, {"dsr_min": 0.0})
    assert loose["noise"][0]["n"] == 1
    assert mcal.dsr_at(r["dsr_inputs"], 50.0, 252) <= mcal.dsr_at(r["dsr_inputs"], 1.0, 252) + 1e-12
    assert r["mined"]["spec"]["kind"] == "bars"
    truth = mcal._truth_one(("bars", r["mined"]["spec"], 5))
    assert truth["record"]["strategy"].startswith("mined_") and "edge" in truth


def test_static_panel_space_and_scoring():
    """A static universe offers its timing rules and no dollar volume; specs score through the weights engine."""
    panel = synthetic_panel(6, 6, 0.0, 2)
    ev = Evaluator.synthetic(panel=panel)
    assert ev.space.rules == grammar.STATIC_RULES and "dvol" not in ev.space.terminals
    spec = grammar.normalize({"signal": {"formula": {"op": "ts_ret", "x": {"op": "close"}, "d": 120}},
                              "hold": {"quantile": 0.3, "rebalance": 21}}, ev.space)
    r = ev.evaluate(spec)
    assert r.ok
    w = grammar.compile_spec(spec, ev.space).weights(panel)
    assert np.isclose(run_portfolio(panel, w).net, run_portfolio(panel, w).net).all()


# ---------------------------------------------------------------------------------------------------------------
# API


def test_mining_api(spy, monkeypatch):
    """The grammar, jobs (queued on the runner), budgets and refusals through the API."""
    client = TestClient(create_app())
    g = client.get("/api/mining/grammar").json()
    assert "ts_mean" in g["operators"]["time_series"] and "crypto_top30" in [u["name"] for u in g["universes"]]
    assert client.post("/api/mining/jobs", json={"name": "SPY", "paradigm": "nope"}).status_code == 400
    assert client.post("/api/mining/jobs", json={"kind": "panel", "name": "nowhere"}).status_code == 400
    assert client.post("/api/mining/jobs", json={"name": "SPY", "banned": ["moon"]}).status_code == 400
    ran = []
    monkeypatch.setattr(jobs, "submit", lambda job_id, log=None: ran.append(jobs.run(job_id, log=lambda m: None)))
    res = client.post("/api/mining/jobs", json={"name": "SPY", "paradigm": "random", "max_trials": 20,
                                                "workers": 1})
    assert res.status_code == 200
    job_id = res.json()["job_id"]
    assert ran[0]["status"] == "done"
    assert client.get(f"/api/mining/jobs/{job_id}").json()["progress"]["n_trials"] == 20
    assert client.get("/api/mining/jobs").json()[0]["job_id"] == job_id
    assert client.get("/api/mining/jobs/nope").status_code == 404
    assert client.post(f"/api/mining/jobs/{job_id}/stop").status_code == 409
    assert client.post(f"/api/mining/jobs/{job_id}/gauntlet", json={"strategy": "mined_x"}).status_code == 404
    assert client.post("/api/mining/budgets", json={"question": "mined:SPY:1d", "max_trials": 50,
                                                    "reason": ""}).status_code == 400
    b = client.post("/api/mining/budgets", json={"question": "mined:SPY:1d", "max_trials": 50,
                                                 "reason": "test"}).json()
    assert b["max_trials"] == 50 and b["used"] == 20
    assert client.get("/api/mining/budgets").json()[0]["question"] == "mined:SPY:1d"
    assert client.get("/api/mining/calibration").json() is None


# ---------------------------------------------------------------------------------------------------------------
# Mined trials counting more than once in N (decision 0011)


def test_mining_n_factor_weights_mined_trials_only():
    """The factor multiplies mined trials in N and nothing else; versions without the key count each trial once."""
    from qlab.gauntlet import pipeline, stats

    assert thresholds.current()[1]["mining_n_factor"] == 2.0  # fresh labs start from thresholds v5's value
    assert thresholds.LEGACY["mining_n_factor"] == 1.0  # versions saved before the key count each trial once
    with pytest.raises(ValueError, match="at least 1"):
        thresholds.update({"mining_n_factor": 0.5}, "too small")
    th = {"mining_n_factor": 3.0}
    assert pipeline.trial_weights(["mined_abc", "trend_sma", "mined_def"], th) == [3.0, 1.0, 3.0]
    rng = np.random.default_rng(4)
    cand = rng.normal(0.0004, 0.01, 2500)
    trials = rng.normal(0, 0.3, 200)
    plain = pipeline.deflate(cand, trials, 252, "luck_max")
    heavy = pipeline.deflate(cand, trials, 252, "luck_max", weight=np.full(200, 3.0))
    assert plain["n_trials"] == 200 and heavy["n_trials"] == 600
    assert heavy["sr0"] > plain["sr0"] and heavy["dsr"] < plain["dsr"]
    assert heavy["var_annual"] == pytest.approx(plain["var_annual"])
    assert stats.expected_max_sharpe(600, 1.0) > stats.expected_max_sharpe(200, 1.0)


def test_a_mined_card_counts_mined_trials_by_the_factor(spy):
    """With the factor in force, a job's luck line and a mined card's N count each mined trial that many times."""
    thresholds.update(FAST, "fast tests")
    rep = jobs.run(jobs.create(spy, "random", 40, 5, workers=1)["job_id"], log=lambda m: None)
    scored = rep["progress"]["n_valid"]
    assert rep["progress"]["N"] == 2 * scored
    card = jobs.send_to_gauntlet(rep["job_id"], rep["top"][0]["strategy"])
    sig = next(s for s in card["stages"] if s["key"] == "significance")
    assert card["meta"]["n_trials"] == 2 * scored and "counts 2 times" in sig["summary"]


# ---------------------------------------------------------------------------------------------------------------
# The nightly rotation


def test_the_rotation_picks_the_least_mined_scope_and_the_weekday_paradigm():
    """Fewest mined trials first (ties to the first scope), a spent budget is skipped, the paradigm follows the day."""
    from datetime import date

    from qlab.mining import nightly as mn

    budgets = [{"question": "mined:all_etfs:1d", "used": 750, "remaining": 1250},
               {"question": "mined:crypto_top30:1d", "used": 500, "remaining": 1500}]
    plan = mn.pick(budgets, ["all_etfs", "crypto_top30"], date(2026, 10, 22))  # a Thursday
    assert plan == {"scope": "crypto_top30", "question": "mined:crypto_top30:1d", "paradigm": "gp", "trials": 250,
                    "seed": 20261022}
    assert mn.pick([], ["all_etfs", "crypto_top30"], date(2026, 10, 20))["scope"] == "all_etfs"  # tie, Tuesday
    assert mn.pick([], ["all_etfs"], date(2026, 10, 20))["paradigm"] == "random"
    spent = [{"question": "mined:all_etfs:1d", "used": 2000, "remaining": 0},
             {"question": "mined:crypto_top30:1d", "used": 1900, "remaining": 100}]
    assert mn.pick(spent, ["all_etfs", "crypto_top30"], date(2026, 10, 21))["trials"] == 100
    assert "skip" in mn.pick(spent[:1], ["all_etfs"], date(2026, 10, 21))
    assert set(mn.PARADIGMS.values()) == {"gp", "random", "tpe"}  # never the uncalibrated llm paradigm


def test_the_nightly_rotation_mines_within_budget_and_waits_without_the_factor(spy, monkeypatch):
    """A night mines what is left of the budget, judges only past the bar, alerts once; below 2x it waits."""
    from qlab import alerts
    from qlab.mining import nightly as mn

    monkeypatch.setenv("QLAB_MINING_SCOPES", "SPY")
    monkeypatch.setattr(mn, "HEAVY", "[q]lab-never-matches-this")
    monkeypatch.setattr("qlab.paper.nightly.api_up", lambda api: False)
    thresholds.update(FAST, "fast tests")
    jobs.set_budget("mined:SPY:1d", 30, "small test budget")
    out = mn.run(workers=1, minutes=5, log=lambda m: None)
    assert out["status"] in ("mined", "judged") and out["plan"]["trials"] == 30
    assert jobs.budget_of("mined:SPY:1d")["remaining"] == 0
    assert out["bar"] is not None and out["clears"] == (out["best"] >= out["bar"])
    assert (out["status"] == "judged") == out["clears"]
    assert alerts.recent(5)[0]["rule"] == "mining_nightly"
    assert mn.run(log=lambda m: None)["status"] == "skipped"  # budget spent
    thresholds.update({"mining_n_factor": 1.0}, "pretend the calibration hasn't settled")
    assert mn.run(log=lambda m: None, notify=False)["status"] == "waiting"
