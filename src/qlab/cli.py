"""Command line entry point: ``qlab serve | init | ingest | backtest | portfolio | gauntlet | portfolio-gauntlet |
calibrate | calibrate-variance | calibrate-mining | bench | demo | paper | report | registry | alerts | broker | inbox |
mine | radar``.
"""

from __future__ import annotations

import json

import typer

from . import config, db

app = typer.Typer(help="quant-lab: evidence-first quant research lab", no_args_is_help=True)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765, reload: bool = False) -> None:
    """Run the Lab API and serve the built web app.

    Args:
        host: Interface to bind. Keep 127.0.0.1 unless you know you need remote access.
        port: Port for the API and the web app.
        reload: Restart on code changes (development only).
    """
    import uvicorn

    uvicorn.run("qlab.api.main:app", host=host, port=port, reload=reload)


@app.command()
def backup(keep: int = typer.Option(3, help="Snapshots to keep"),
           api: str = typer.Option("http://127.0.0.1:8765/api", help="Lab API; the snapshot is taken there when up")
           ) -> None:
    """Write a consistent snapshot of the lab database into <data>/backups and keep the newest few.

    Args:
        keep: Snapshots to keep.
        api: Lab API address.
    """
    import httpx

    from . import ops
    from .paper.nightly import api_up

    if api_up(api):
        res = httpx.post(f"{api}/ops/backup", timeout=1800)
        res.raise_for_status()
        out = res.json()
    else:
        out = ops.backup(keep, actor="cli")
    typer.echo(f"snapshot {out['path']} ({out['bytes'] / 2**20:,.0f} MB, {out['seconds']} s)"
               + (f"; removed {', '.join(out['removed'])}" if out["removed"] else ""))


@app.command()
def heartbeat(api: str = typer.Option("http://127.0.0.1:8765/api", help="Lab API to check")) -> None:
    """Check that the lab answers and reported after the last close, and ping QLAB_HEALTHCHECK_URL. Exits 1 on a
    problem.

    Args:
        api: Lab API address.
    """
    from . import ops

    out = ops.heartbeat(api)
    typer.echo(("ok" if out["ok"] else "PROBLEM: " + "; ".join(out["problems"]))
               + ("" if out["pinged"] else " (no outside check pinged)"))
    if not out["ok"]:
        raise typer.Exit(1)


@app.command()
def init() -> None:
    """Create the data folders and the database schema."""
    config.ensure_dirs()
    with db.connect():
        pass
    typer.echo(f"Lab initialised at {config.data_dir()}")


@app.command()
def ingest(
    source: str = typer.Option("all", help="binance, yahoo, french, or all"),
    symbols: str = typer.Option("", help="Comma-separated symbols; empty means the default universe"),
    universe: str = typer.Option("", help="usdt: every Binance USDT pair, delisted ones included (1d bars)"),
) -> None:
    """Download free data, store it as Parquet and run the quality checks.

    Args:
        source: Which source to ingest.
        symbols: Optional symbol filter.
        universe: ``usdt`` ingests the whole Binance USDT cross-section instead of the default symbols.
    """
    from .data.ingest import SOURCES, ingest_usdt_universe
    from .data.ingest import ingest as run

    if universe:
        if universe != "usdt" or source != "binance":
            raise typer.BadParameter("--universe usdt works with --source binance only")
        res = ingest_usdt_universe(log=typer.echo, actor="cli")
        m = res["manifest"]
        typer.echo(f"== binance usdt: {res['status']} ({len(res['series'])} series, {len(res['errors'])} errors; "
                   f"{len(m['excluded'])} of {m['n_pairs']} pairs left out)")
        return
    names = list(SOURCES) if source == "all" else [source]
    wanted = [s.strip() for s in symbols.split(",") if s.strip()] or None
    for name in names:
        res = run(name, wanted, log=typer.echo, actor="cli")
        typer.echo(f"== {name}: {res['status']} ({len(res['series'])} series, {len(res['errors'])} errors)")


def _parse_params(text: str) -> dict[str, float]:
    """Parse ``"window=200,entry_band=0.01"`` into a dict.

    Args:
        text: Comma-separated ``name=value`` pairs.

    Returns:
        Mapping of parameter name to float.
    """
    out: dict[str, float] = {}
    for pair in filter(None, (p.strip() for p in text.split(","))):
        name, _, value = pair.partition("=")
        out[name.strip()] = float(value)
    return out


@app.command()
def backtest(
    symbol: str,
    strategy: str = typer.Option("trend_sma", help="Registry key; see /api/backtest/strategies"),
    source: str = typer.Option("yahoo", help="Data source of the symbol"),
    interval: str = typer.Option("1d", help="Bar interval"),
    params: str = typer.Option("", help="Overrides, e.g. window=200,entry_band=0.01"),
    costs: str = typer.Option("auto", help="auto, etf, crypto or zero"),
    start: str = typer.Option("", help="First date, YYYY-MM-DD"),
    end: str = typer.Option("", help="Last date, YYYY-MM-DD"),
) -> None:
    """Backtest one strategy, record the trial, and print the headline numbers next to buy-and-hold.

    Args:
        symbol: Instrument, e.g. SPY or BTCUSDT.
        strategy: Strategy name.
        source: Data source.
        interval: Bar interval.
        params: Parameter overrides.
        costs: Cost preset.
        start: First date.
        end: Last date.
    """
    from .backtest import runner

    result, summary = runner.backtest(source, symbol, interval, strategy, _parse_params(params), costs,
                                      start or None, end or None)
    rep = runner.report(result, summary)
    s, b = rep["stats"]["strategy"], rep["stats"]["benchmark"]
    typer.echo(f"{summary['question']}  {summary['start']} -> {summary['end']}  trial {summary['trial_id']} "
               f"(N = {summary['n_trials']} on this question)")
    for label, key, fmt in [("CAGR", "cagr", "{:+.2%}"), ("Sharpe", "sharpe", "{:.2f}"),
                            ("Max drawdown", "max_dd", "{:.1%}"), ("Volatility", "vol", "{:.1%}")]:
        typer.echo(f"  {label:<13} strategy {fmt.format(s[key]):>9}   buy-and-hold {fmt.format(b[key]):>9}")
    typer.echo(f"  trades {rep['stats']['n_trades']}, exposure {s.get('exposure', 0):.0%}, "
               f"cost drag {sum(rep['costs']['per_year'].values()):.2%}/yr")


@app.command()
def gauntlet(
    symbol: str,
    strategy: str = typer.Option("trend_sma", help="Registry key; see /api/backtest/strategies"),
    source: str = typer.Option("yahoo", help="Data source of the symbol"),
    interval: str = typer.Option("1d", help="Bar interval"),
    params: str = typer.Option("", help="Candidate parameters, e.g. window=200,entry_band=0.01"),
    costs: str = typer.Option("auto", help="auto, etf, crypto or zero"),
    seed: int = typer.Option(7, help="Seed for the grid fill, permutations and bootstrap"),
) -> None:
    """Run a candidate through the ten-stage gauntlet and print its report card.

    Args:
        symbol: Instrument, e.g. SPY or BTCUSDT.
        strategy: Strategy name.
        source: Data source.
        interval: Bar interval.
        params: Candidate parameters.
        costs: Cost preset.
        seed: Random seed.
    """
    from .gauntlet.pipeline import run_gauntlet

    _print_card(run_gauntlet(source, symbol, interval, strategy, _parse_params(params), costs, seed, actor="cli"))


@app.command()
def portfolio(
    universe: str = typer.Argument("multi_asset", help="Universe name; see /api/portfolio/universes"),
    strategy: str = typer.Option("tsmom", help="Portfolio strategy; see /api/portfolio/strategies"),
    params: str = typer.Option("", help="Overrides, e.g. lookback=252,target_vol=0.1"),
    include_lockbox: bool = typer.Option(False, help="Reach into the sealed period (spends this question's lockbox)"),
) -> None:
    """Backtest a portfolio strategy, record the trial, and print it next to equal weight.

    Args:
        universe: Universe name.
        strategy: Portfolio strategy key.
        params: Parameter overrides.
        include_lockbox: Include the sealed lockbox period.
    """
    from .portfolio import runner

    panel, res, summary = runner.backtest(universe, strategy, _parse_params(params), include_lockbox=include_lockbox)
    rep = runner.report(panel, res, summary)
    s, b = rep["stats"]["strategy"], rep["stats"]["benchmark"]
    typer.echo(f"{summary['question']}  {summary['start']} -> {summary['end']}  trial {summary['trial_id']} "
               f"(N = {summary['n_trials']})  {len(panel.symbols)} instruments")
    for label, key, fmt in [("CAGR", "cagr", "{:+.2%}"), ("Sharpe", "sharpe", "{:.2f}"),
                            ("Max drawdown", "max_dd", "{:.1%}"), ("Volatility", "vol", "{:.1%}")]:
        typer.echo(f"  {label:<13} strategy {fmt.format(s[key]):>9}   equal weight {fmt.format(b[key]):>9}")
    typer.echo(f"  edge Sharpe {s['edge_sharpe']:.2f}, beta {s['beta']:.2f}, gross exposure {s['gross_exposure']:.2f}, "
               f"turnover {s['turnover']:.1f}/yr")


@app.command("portfolio-gauntlet")
def portfolio_gauntlet(
    universe: str = typer.Argument("multi_asset", help="Universe name"),
    strategy: str = typer.Option("tsmom", help="Portfolio strategy"),
    params: str = typer.Option("", help="Candidate parameters"),
    seed: int = typer.Option(7, help="Seed for the grid fill, permutations and bootstrap"),
) -> None:
    """Run a portfolio strategy through the ten-stage gauntlet and print its report card.

    Args:
        universe: Universe name.
        strategy: Portfolio strategy key.
        params: Candidate parameters.
        seed: Random seed.
    """
    from .gauntlet.pipeline import run_portfolio_gauntlet

    _print_card(run_portfolio_gauntlet(universe, strategy, _parse_params(params), seed, actor="cli"))


@app.command("portfolio-batch")
def portfolio_batch(
    universes: str = typer.Option("asset_classes_long,all_etfs", help="Comma-separated universe names"),
    strategies: str = typer.Option("tsmom,trend_filter,xs_momentum,risk_parity,vol_managed",
                                   help="Comma-separated portfolio strategies, judged at their published defaults"),
    note: str = typer.Option(..., help="Why this batch, recorded before anything runs"),
    seed: int = typer.Option(7, help="Random seed"),
) -> None:
    """Judge a pre-registered batch of portfolio strategies at their defaults, deflated against the batch.

    Args:
        universes: Universe names.
        strategies: Strategy keys.
        note: The pre-registration note.
        seed: Random seed.
    """
    from . import lockbox
    from .gauntlet.pipeline import judge_batch
    from .gauntlet.subjects import PanelSubject

    sealed = lockbox.sealed_from()
    entries = [(PanelSubject.load(u.strip(), s.strip(), sealed), None)
               for u in universes.split(",") for s in strategies.split(",")]
    for card in judge_batch(entries, note, seed, actor="cli"):
        _print_card(card)
        typer.echo("")


def _print_card(card: dict) -> None:
    """Print a report card as one line per stage.

    Args:
        card: Report card.
    """
    m = card["meta"]
    typer.echo(f"{m['question']}  card {card['card_id']}  research {m['research'][0]} -> {m['research'][1]}  "
               f"N = {m['n_trials']}  ({m['elapsed_s']} s)")
    marks = {True: "PASS", False: "FAIL", None: " -- "}
    for s in card["stages"]:
        typer.echo(f"  [{marks[s['passed']]}] {s['name']:<22} {s['summary']}")
    typer.echo(f"Verdict: {card['verdict'].upper()}" + (f" at {card['failed_stage']}" if card["failed_stage"] else ""))


@app.command()
def calibrate(
    noise_reps: int = typer.Option(100, help="Runs per family and researcher type on noise markets"),
    power_reps: int = typer.Option(40, help="Runs per family, edge size and history length on planted trends"),
    workers: int = typer.Option(0, help="Worker processes; 0 uses all cores but one"),
    seed: int = typer.Option(1000, help="Base seed; confirm tuned thresholds with a different one"),
    portfolio_reps: int = typer.Option(40, help="Noise runs per portfolio family and researcher type; 0 skips"),
    xs_reps: int = typer.Option(0, help="Noise runs per crypto cross-section family, size and researcher; 0 skips"),
    xs_power_reps: int = typer.Option(15, help="Planted-edge runs per crypto cross-section setting"),
    hourly_reps: int = typer.Option(0, help="Noise runs per intraday family and researcher on hourly markets; 0 skips"),
    hourly_power_reps: int = typer.Option(20, help="Planted-momentum runs per edge size for intraday momentum"),
) -> None:
    """Measure the gauntlet's false-positive rate on noise and its power on planted edges.

    Args:
        noise_reps: Noise runs per family and researcher type.
        power_reps: Planted-edge runs per setting.
        workers: Worker processes.
        seed: Base seed for the simulated markets.
        portfolio_reps: Noise runs per portfolio family and researcher type.
        xs_reps: Noise runs per crypto cross-section family, universe size and researcher type.
        xs_power_reps: Planted-edge runs per crypto cross-section family, edge size and history length.
        hourly_reps: Noise runs per intraday family and researcher type (0 skips them).
        hourly_power_reps: Planted-momentum runs per edge size for the intraday momentum rule.
    """
    from .gauntlet.calibration import run_calibration

    rep = run_calibration(noise_reps, power_reps, workers=workers or None, log=typer.echo, actor="cli",
                          base_seed=seed, portfolio_reps=portfolio_reps, xs_reps=xs_reps,
                          xs_power_reps=xs_power_reps, hourly_reps=hourly_reps,
                          hourly_power_reps=hourly_power_reps)
    typer.echo(f"\nReport {rep['id']} ({rep['elapsed_s']} s, {rep['settings']['n_trials']:,} trials recorded)")
    for r in rep["noise"]:
        typer.echo(f"  noise  {r['family']:<26} {r['scenario']:<12} {r['admitted']:>3}/{r['n']:<4} "
                   f"{r['rate']:6.1%}  [{r['low']:.1%}, {r['high']:.1%}]")
    for r in rep["power"]:
        typer.echo(f"  power  {r['family']:<26} edge {r['true_edge']:.2f}  {r['years']:>4g} y  "
                   f"{r['admitted']:>3}/{r['n']:<4} {r['rate']:6.1%}")
    typer.echo(rep["headline"])
    for basis, s in rep["by_regime_basis"].items():
        worst = max(s["noise"], key=lambda r: r["rate"]) if s["noise"] else None
        typer.echo(f"\nRegimes judging the {basis}: noise {s['fpr_overall']:.2%} pooled"
                   + (f", worst {worst['scenario']} {worst['family']} {worst['rate']:.1%}" if worst else ""))
        for r in s["power"]:
            typer.echo(f"  power  {r['family']:<28} edge {r['true_edge']:.2f}  {r['years']:>4g} y  "
                       f"{r['admitted']:>3}/{r['n']:<4} {r['rate']:6.1%}")


@app.command("calibrate-variance")
def calibrate_variance(
    seed: int = typer.Option(210_000, help="Base seed; confirm a choice with a different one"),
    workers: int = typer.Option(0, help="Worker processes; 0 uses all cores but one"),
    scale: float = typer.Option(1.0, help="Multiply every run count (0.1 for a quick look)"),
) -> None:
    """Compare ways of estimating the Deflated Sharpe's cross-trial variance on grids and pre-registered batches.

    Args:
        seed: Base seed for the simulated markets.
        workers: Worker processes.
        scale: Factor on every count in ``calibration.STUDY_REPS``; a part with a count above 0 keeps at least 1.
    """
    from .gauntlet.calibration import STUDY_REPS, run_variance_study

    reps = {k: max(1, round(v * scale)) if v else 0 for k, v in STUDY_REPS.items()}
    rep = run_variance_study(reps, workers=workers or None, log=typer.echo, actor="cli", base_seed=seed)
    typer.echo(f"\nReport {rep['id']} ({rep['elapsed_s']} s, {rep['settings']['n_trials']:,} trials recorded)")
    for method, s in rep["by_method"].items():
        typer.echo(f"\n{method}: noise {s['fpr_overall']:.2%} pooled")
        for r in sorted(s["noise"], key=lambda r: -r["rate"])[:5]:
            typer.echo(f"  noise  {r['scenario']:<12} {r['family']:<28} {r['admitted']:>3}/{r['n']:<4} "
                       f"{r['rate']:6.1%}  [{r['low']:.1%}, {r['high']:.1%}]")
        for r in s["power"]:
            typer.echo(f"  power  {r['scenario']:<12} {r['family']:<28} edge {r['true_edge']:.2f}  "
                       f"{r['admitted']:>3}/{r['n']:<4} {r['rate']:6.1%}")


@app.command()
def bench(repeats: int = 20) -> None:
    """Time the engine on daily and hourly bars, to ground decision 0001 (Python first, Rust if measured).

    Args:
        repeats: Runs per case; the median is reported.
    """
    import statistics
    import time as _time

    from .backtest import runner
    from .backtest.engine import COST_PRESETS, run
    from .backtest.strategies import REGISTRY

    cases = [("yahoo", "SPY", "1d"), ("binance", "BTCUSDT", "1h")]
    for source, symbol, interval in cases:
        try:
            bars = runner.load_bars(source, symbol, interval)
        except FileNotFoundError:
            typer.echo(f"{source}/{symbol}/{interval}: not ingested, skipped")
            continue
        strat = REGISTRY["trend_sma"]
        times = []
        for _ in range(repeats):
            t0 = _time.perf_counter()
            run(bars, strat.positions(bars), COST_PRESETS["etf"])
            times.append(_time.perf_counter() - t0)
        ms = statistics.median(times) * 1000
        typer.echo(f"{symbol} {interval}: {len(bars):>7,} bars  {ms:8.1f} ms per backtest  "
                   f"~{1000 / ms * 15:,.0f} backtests/s on 15 cores")


@app.command()
def demo() -> None:
    """Run the demo pipeline once and print the result as JSON."""
    from .api.routes_core import run_demo

    typer.echo(json.dumps(run_demo(), indent=2, default=str))


paper_app = typer.Typer(help="Forward paper book: record each day's targets before the outcome, then score them",
                        no_args_is_help=True)
app.add_typer(paper_app, name="paper")


@paper_app.command("add")
def paper_add(
    cards: list[str] = typer.Argument(..., help="Gauntlet report card ids (any verdict)"),
    note: str = typer.Option("", help="Why these cards are tracked"),
) -> None:
    """Put report cards in the paper book and freeze what their research periods expect.

    Args:
        cards: Card ids.
        note: Reason, recorded with each entry.
    """
    from .paper import book

    for card_id in cards:
        try:
            e = book.add(card_id, note, actor="cli")
        except (book.Refused, FileNotFoundError) as err:
            typer.echo(f"{card_id}: not added: {err}")
            continue
        ref = e["reference"]
        typer.echo(f"{card_id}: entry {e['entry_id']} for {e['question']} ({e['verdict']}), research edge Sharpe "
                   f"{ref['edge_sharpe']:.2f}, beta {ref['beta']:.2f}")


@paper_app.command("run")
def paper_run() -> None:
    """Decide every active entry for the latest final close. Safe to repeat; backfills are refused."""
    from .paper import book

    results = book.run(actor="cli")
    for r in results:
        typer.echo(f"  [{r['status']:<8}] {r['question']:<34} {r['message']}")
    typer.echo(f"{sum(r['status'] == 'decided' for r in results)} decided, "
               f"{sum(r['status'] == 'exists' for r in results)} already decided, "
               f"{sum(r['status'] in ('refused', 'error') for r in results)} refused or failed")


@paper_app.command("score")
def paper_score() -> None:
    """Replay every entry's decisions on the bars after them and compare with the research expectations."""
    from .paper import score

    for r in score.score(actor="cli"):
        if r["status"] != "scored":
            typer.echo(f"  [error   ] {r['question']:<34} {r['message']}")
            continue
        edge = "–" if r["forward_edge_sharpe"] is None else f"{r['forward_edge_sharpe']:+.2f}"
        typer.echo(f"  [{'ALARM' if r['alarm'] else 'scored':<8}] {r['question']:<34} {r['message']}, forward edge "
                   f"Sharpe {edge} ({r['band'] or 'no band yet'})")


@paper_app.command("list")
def paper_list() -> None:
    """Show the book: every entry, its status and its latest decision."""
    from .paper import book

    for e in book.entries():
        decs = book.decisions(e["entry_id"])
        last = f"last decision {decs[-1]['as_of']}" if decs else "no decision yet"
        typer.echo(f"  {e['entry_id']}  {e['status']:<8} {e['question']:<34} card {e['card_id']} ({e['verdict']}), "
                   f"{len(decs)} decisions, {last}")


@paper_app.command("retire")
def paper_retire(entry_id: str, reason: str = typer.Option(..., help="Why the entry stops")) -> None:
    """Stop an entry: no new decisions, scoring ends now, its record stays.

    Args:
        entry_id: Entry id.
        reason: Why, recorded in the audit log.
    """
    from .paper import book

    e = book.retire(entry_id, reason, actor="cli")
    typer.echo(f"{entry_id}: retired at {e['retired_at']}")


@paper_app.command("nightly")
def paper_nightly(
    api: str = typer.Option("http://127.0.0.1:8765/api", help="Lab API to use when it is up"),
    report: bool = typer.Option(True, help="Build the daily report and raise its alerts at the end"),
    wait_minutes: float = typer.Option(0, help="Most minutes to wait for a late US close before deciding"),
    retry_minutes: float = typer.Option(20, help="Minutes between Yahoo re-ingests while waiting"),
) -> None:
    """Ingest the free sources, decide every active entry, score the book and build the daily report. Exits 1 if a
    step failed.

    Uses the running Lab API when it answers (it holds the database), otherwise runs everything here.

    Args:
        api: Base URL of the Lab API.
        report: Build the daily report and its alerts.
        wait_minutes: Most minutes to wait for Yahoo to publish the latest finished session.
        retry_minutes: Minutes between tries while waiting.
    """
    from .paper.nightly import nightly
    from .timeutil import now_utc

    typer.echo(f"== {now_utc().isoformat(timespec='seconds')}")
    res = nightly(api, log=typer.echo, report=report, wait=wait_minutes * 60, retry=retry_minutes * 60)
    for r in res["ingest"]:
        typer.echo(f"  ingest {r['source']:<8} {r['status']} ({r['n_series']} series)")
    waited = res.get("wait") or {}
    if waited.get("tries"):
        typer.echo(f"  wait   {waited['tries']} Yahoo re-ingests over {waited['waited'] / 60:.0f} minutes; "
                   + ("up to date" if waited["fresh"] else f"still behind: {waited['message']}"))
    for r in res["run"]:
        typer.echo(f"  run    [{r['status']:<8}] {r['question']:<34} {r['message']}")
    for r in res["score"]:
        typer.echo(f"  score  [{'ALARM' if r.get('alarm') else r['status']:<8}] {r['question']:<34} {r['message']}")
    broker = res.get("broker") or {}
    synced = broker.get("sync") or {}
    if synced:
        typer.echo(f"  broker sync: {synced.get('updated', 0)} orders updated, {synced.get('fills', 0)} new fills"
                   + "".join(f", {k} {'reconciled' if v.get('ok') else 'MISMATCH'}"
                             for k, v in (synced.get("reconciliation") or {}).items()))
    for r in broker.get("execute") or []:
        typer.echo(f"  broker [{r['status']:<14}] {r['entry_id']} decision {r['as_of']}: "
                   f"{len(r['orders'])} orders" + (f"; {r['notes'][0]}" if r["notes"] else ""))
        for o in r["orders"]:
            typer.echo(f"      {o['side']:<4} {o['qty']:g} {o['symbol']:<9} {o['tif']:<4} {o['status']}"
                       + (f"  {o['message'][:80]}" if o["message"] else ""))
    if res.get("error"):
        typer.echo(f"  error  {res['error']}")
    rep = res.get("report") or {}
    if rep.get("error"):
        typer.echo(f"  report failed: {rep['error']} (alert sent without the database)")
    elif rep:
        typer.echo(f"  report {rep['report_id']}")
        for h in rep["headline"]:
            typer.echo(f"    {h['text']}")
        for a in rep["alerts"]:
            delivery = ", ".join(f"{k} {v}" for k, v in a["delivery"].items())
            typer.echo(f"  alert  [{a['severity']:<4}] {a['title']}  ({delivery})")
    if not res["ok"]:
        raise typer.Exit(1)


@paper_app.command("launchd")
def paper_launchd(
    hour: int = typer.Option(-1, help="Local hour to run at; -1 picks a time after the New York close all year"),
    minute: int = typer.Option(0, help="Local minute"),
) -> None:
    """Write the nightly job as a launchd plist for this checkout and print how to install it. Installs nothing.

    Args:
        hour: Local hour, or -1 for the default.
        minute: Local minute (used with an explicit hour).
    """
    from .paper.nightly import default_time, render_launchd

    if hour < 0:
        hour, minute = default_time()
    path, steps = render_launchd(config.data_dir() / "ops", hour, minute)
    typer.echo(f"Wrote {path} (runs daily at {hour:02d}:{minute:02d} local time). To install it yourself:\n")
    typer.echo("\n".join(steps))


@app.command()
def report(
    notify: bool = typer.Option(True, help="Deliver alerts to every channel; --no-notify keeps them in the file only"),
    digest: bool = typer.Option(False, help="Also send the headline as one message through the channels"),
    api: str = typer.Option("http://127.0.0.1:8765/api", help="Lab API to use when it is up"),
) -> None:
    """Build the daily lab report for the time since the last one, print it, and raise its alerts.

    Uses the running Lab API when it answers (it holds the database), otherwise builds it here.

    Args:
        notify: Deliver alerts beyond the file channel.
        digest: Send the headline as a message too.
        api: Base URL of the Lab API.
    """
    import httpx

    from . import report as daily
    from .paper.nightly import api_up

    if api_up(api):
        res = httpx.post(f"{api}/report", json={"trigger": "cli", "notify": notify, "digest": digest}, timeout=120)
        res.raise_for_status()
        rep = res.json()
    else:
        rep = daily.build(trigger="cli", notify=notify, send_digest=digest, actor="cli")
    typer.echo(daily.render_text(rep))


registry_app = typer.Typer(help="Strategy registry: every report card with its lifecycle state", no_args_is_help=True)
app.add_typer(registry_app, name="registry")


@registry_app.command("list")
def registry_list(state: str = typer.Option("", help="Only this state, e.g. near_miss or in_paper_book")) -> None:
    """Show every report card with its lifecycle state, grouped by state.

    Args:
        state: Show one state only; empty shows all.
    """
    from . import registry

    items = registry.cards()
    for s in registry.STATES:
        group = [c for c in items if c["state"] == s]
        if (state and s != state) or not group:
            continue
        typer.echo(f"{registry.LABELS[s]} ({len(group)})")
        for c in group:
            nums = (f"edge {c['edge']:+.2f}" if c["edge"] is not None else "edge –") + (
                f", p {c['perm_p']:.3f}" if c["perm_p"] is not None else "") + (
                f", PSR {c['psr']:.2f}" if c["psr"] is not None else "")
            failed = ", ".join(c["failed"]) or "none"
            typer.echo(f"  {c['card_id']}  {c['question']:<38} {c['verdict']:<9} {nums:<34} failed: {failed}")


@registry_app.command("retire")
def registry_retire(card_id: str, reason: str = typer.Option(..., help="Why the card is retired")) -> None:
    """Retire a report card; an active paper entry for it stops too.

    Args:
        card_id: Report card id.
        reason: Why, recorded in the audit log.
    """
    from . import registry

    c = registry.retire(card_id, reason, actor="cli")
    typer.echo(f"{card_id}: {c['label'].lower()} since {c['since']}")


alerts_app = typer.Typer(help="Alerts: the log, the channels, and a test message", no_args_is_help=True)
app.add_typer(alerts_app, name="alerts")


@alerts_app.command("list")
def alerts_list(limit: int = typer.Option(20, help="How many alerts")) -> None:
    """Show the latest alerts and what each channel did with them.

    Args:
        limit: Number of alerts.
    """
    from . import alerts

    for a in alerts.recent(limit):
        delivery = ", ".join(f"{k} {v}" for k, v in a["delivery"].items())
        typer.echo(f"  {a['created_at']}  [{a['severity']:<4}] {a['title']}  ({delivery})")


@alerts_app.command("channels")
def alerts_channels() -> None:
    """Show which channels deliver alerts (never prints a token)."""
    from . import alerts

    for c in alerts.channel_status():
        typer.echo(f"  {c['name']:<9} {'on ' if c['enabled'] else 'off'}  {c['detail']}")


@alerts_app.command("send")
def alerts_send(title: str = typer.Option(..., help="One line"),
                message: str = typer.Option("", help="Details"),
                severity: str = typer.Option("warn", help="info, good, warn or bad")) -> None:
    """Send one alert through the channels without the database (for systemd's OnFailure hook on the server).

    Args:
        title: One line.
        message: Details.
        severity: Severity.
    """
    from . import alerts
    from .timeutil import now_utc

    item = alerts.Alert(rule="server", severity=severity, key=f"server:{now_utc().isoformat()}", title=title,
                        message=message, link="/system")
    for d in alerts.deliver([item]):
        typer.echo(", ".join(f"{k} {v}" for k, v in d.items()))


@alerts_app.command("test")
def alerts_test() -> None:
    """Send a test alert through every channel and print what each one did."""
    from . import alerts

    row = alerts.send_test(actor="cli")
    for name, result in row["delivery"].items():
        typer.echo(f"  {name:<9} {result}")


informed_app = typer.Typer(help="Pipeline C (M5b): signals from corporate insiders, from the SEC's Form 4 data sets",
                           no_args_is_help=True)
app.add_typer(informed_app, name="informed")


@informed_app.command("ingest")
def informed_ingest() -> None:
    """Download the SEC's quarterly insider data sets not cached yet and store the daily insider series.

    Needs QLAB_SEC_USER_AGENT with a contact address (the SEC's rule for automated clients). The first run downloads
    about 80 quarterly files; later runs fetch only new quarters.
    """
    from .informed import insiders

    out = insiders.build(log=typer.echo, actor="cli")
    typer.echo(f"sec/INSIDERS/1d: {out['n_rows']} filing days from {str(out['first_ts'])[:10]} to "
               f"{str(out['last_ts'])[:10]}, {out['quarters']} quarters"
               + (f"; not published yet: {', '.join(out['missing'])}" if out["missing"] else ""))


@informed_app.command("show")
def informed_show(window: int = typer.Option(60, help="Days of filings per reading"),
                  lookback: int = typer.Option(730, help="Days to standardise against")) -> None:
    """Print insider sentiment at each year's end and today (no backtest, so no trial).

    Args:
        window: Days of filings per reading.
        lookback: Days of history to standardise against.
    """
    from .informed import insiders

    s = insiders.series()
    z = insiders.sentiment(window, lookback).dropna()
    yearly = z.groupby(z.index.year).last()
    totals = s.groupby(s.index.year)[["buy_filings", "sell_filings"]].sum()
    for y, v in yearly.items():
        b, sl = totals.loc[y]
        typer.echo(f"  {y}: {int(b):>6} purchases, {int(sl):>7} sales; reading at year end {v:+.2f}")
    typer.echo(f"latest reading {z.index[-1].date()}: {z.iloc[-1]:+.2f} standard deviations")


golive_app = typer.Typer(help="Go-live gate (M8): the evidence checklist before any real money, and your plan",
                         no_args_is_help=False, invoke_without_command=True)
app.add_typer(golive_app, name="golive")


@golive_app.callback()
def golive_show(ctx: typer.Context, entry_id: str = typer.Option("", "--entry", help="Only this paper-book entry")
                ) -> None:
    """Show the go-live checklist of every active paper-book entry (or one), item by item.

    Args:
        ctx: Typer context (a subcommand skips the listing).
        entry_id: One entry.
    """
    if ctx.invoked_subcommand:
        return
    from . import golive

    rows = [golive.checklist(entry_id)] if entry_id else golive.overview()["entries"]
    if not rows:
        typer.echo("No active paper-book entry yet.")
    for r in rows:
        typer.echo(f"{r['entry_id']}  {r['question']}  ({r['verdict']})  {r['met']} of {r['total']} met")
        for i in r["items"]:
            mark = {True: "ok ", False: "NO ", None: " ? "}[i["ok"]]
            typer.echo(f"   {mark} {i['label']}: {i['detail']}")
        typer.echo(f"   next: {r['next_step']}")


@golive_app.command("plan")
def golive_plan(entry_id: str,
                capital: float = typer.Option(..., help="USD you would put in"),
                loss_budget: float = typer.Option(..., help="The most you accept to lose, USD"),
                stop_rule: str = typer.Option("", help="When to stop; empty uses the suggested rule"),
                affirm: bool = typer.Option(False, help="This is money you can afford to lose entirely"),
                reason: str = typer.Option(..., help="Why"),
                confirm: str = typer.Option(..., help="Your name, typed, to sign the plan")) -> None:
    """Write down your plan for an entry. It changes nothing by itself; it is one of the checklist's items.

    Args:
        entry_id: Paper-book entry.
        capital: Money in, USD.
        loss_budget: Maximum loss, USD.
        stop_rule: Stop rule.
        affirm: Affordability affirmed.
        reason: Why.
        confirm: Owner's name.
    """
    from . import golive

    owner = golive.overview()["owner"]
    if confirm.strip() != owner:
        raise typer.BadParameter(f"type your name ({owner}) to sign the plan")
    golive.write_plan(entry_id, capital, loss_budget, stop_rule, affirm, reason, actor="cli")
    r = golive.checklist(entry_id)
    typer.echo(f"plan saved; {r['met']} of {r['total']} items met. {r['next_step']}")


bot_app = typer.Typer(help="Telegram bot: read the lab and reduce risk from a phone; a watchdog for quiet nights",
                      no_args_is_help=True)
app.add_typer(bot_app, name="bot")


@bot_app.command("run")
def bot_run(api: str = typer.Option("http://127.0.0.1:8765/api", help="Lab API the bot reads")) -> None:
    """Answer the owner's Telegram messages and watch the lab, until stopped (a service on the server).

    Args:
        api: Lab API address.
    """
    from . import bot

    bot.run(api, log=lambda m: typer.echo(m, err=True))


@bot_app.command("setup")
def bot_setup(wait: float = typer.Option(180, help="Seconds to wait for your first message to the bot")) -> None:
    """Connect your Telegram bot: paste its token (hidden), message the bot once, and both are saved to lab.env.

    Args:
        wait: Seconds to wait for your message.
    """
    from . import bot, config

    token = config.setting("QLAB_TELEGRAM_TOKEN") or ""
    if not token:
        token = typer.prompt("Paste the token @BotFather gave you (it won't be shown)", hide_input=True).strip()
    tg = bot.Telegram(token, "")
    try:
        me = tg.me()
    except bot.TelegramError as err:
        raise typer.BadParameter(f"Telegram didn't accept that token: {err}") from None
    config.save_setting("QLAB_TELEGRAM_TOKEN", token)
    typer.echo(f"Token works: the bot is @{me.get('username')}. Saved in {config.env_file()} (only you can read it).")
    typer.echo(f"Now open https://t.me/{me.get('username')} on your phone and press Start (or send 'hi').")
    chat = bot.wait_for_owner(tg, wait)
    if chat is None:
        raise typer.BadParameter("no message arrived; run `qlab bot setup` again after writing to the bot")
    config.save_setting("QLAB_TELEGRAM_CHAT", chat["id"])
    bot.Telegram(token, chat["id"]).send("✅ quant-lab is connected. Send /help to see what I can do.")
    typer.echo(f"Connected to {chat['name'] or 'your chat'} (chat id {chat['id']}); a test message is on its way.")


@app.command("config")
def config_set(name: str = typer.Argument(..., help="Setting, e.g. QLAB_HEALTHCHECK_URL"),
               value: str = typer.Argument("", help="Value; leave empty to type it hidden")) -> None:
    """Save one setting in lab.env (readable by you only), e.g. the healthchecks.io URL or the SEC user agent.

    Args:
        name: Setting name.
        value: Its value; prompted for without echo when empty (for secrets).
    """
    from . import config as cfg

    if not value:
        value = typer.prompt(f"Value for {name} (it won't be shown)", hide_input=True).strip()
    path = cfg.save_setting(name.strip(), value)
    typer.echo(f"saved {name} in {path}")


@bot_app.command("chats")
def bot_chats(save: bool = typer.Option(False, help="Write the only private chat's id into lab.env")) -> None:
    """List the chats that wrote to the bot, to find QLAB_TELEGRAM_CHAT; optionally save it.

    Args:
        save: Save the chat id when exactly one private chat wrote.
    """
    from . import bot, config

    token = config.setting("QLAB_TELEGRAM_TOKEN")
    if not token:
        raise typer.BadParameter(f"put QLAB_TELEGRAM_TOKEN=<token from @BotFather> in {config.env_file()} first")
    tg = bot.Telegram(token, "")
    typer.echo(bot.describe_chats(tg))
    if save:
        private = [c for c in tg.chats() if c["type"] == "private"]
        if len(private) != 1:
            raise typer.BadParameter(f"{len(private)} private chats wrote to the bot; set QLAB_TELEGRAM_CHAT by hand")
        path = config.save_setting("QLAB_TELEGRAM_CHAT", private[0]["id"])
        typer.echo(f"saved QLAB_TELEGRAM_CHAT in {path}; run `qlab alerts test` to check")


broker_app = typer.Typer(help="Broker paper trading (Alpaca paper, Binance testnet): sleeves, orders, kill switch",
                         no_args_is_help=True)
app.add_typer(broker_app, name="broker")


@broker_app.command("status")
def broker_status() -> None:
    """Show the controls, each paper account, each sleeve and the latest orders. Never sends an order."""
    from .broker import execution

    st = execution.status()
    c = st["controls"]
    typer.echo(f"trading: {c['mode'].upper()}" + (f" since {c['since']} ({c['reason']})" if c["since"] else ""))
    for p in c["pending"]:
        typer.echo(f"  pending: {p['action']} {p['scope']} takes effect {p['effective_at']} ({p['reason']})")
    for name, b in st["brokers"].items():
        if not b["configured"]:
            typer.echo(f"{name}: not configured")
            continue
        if "error" in b:
            typer.echo(f"{name}: {b['error']}")
            continue
        a, rec = b["account"], b["reconciliation"]
        typer.echo(f"{name}: equity {a['equity']:,.2f} {a['currency']}, cash {a['cash']:,.2f}, "
                   f"{len(b['holdings'])} holdings" + (f", reconciliation {'ok' if rec['ok'] else 'MISMATCH'}"
                                                      if rec else ""))
    for sl in st["sleeves"]:
        held = ", ".join(f"{s} {q:g}" for s, q in sl["holdings"].items()) or "cash only"
        typer.echo(f"  sleeve {sl['entry_id']} ({sl['broker']}, {sl['status']}, {sl['state']}): equity "
                   f"{sl['equity']:,.2f} ({sl['return']:+.2%}); {held}")
    for o in st["orders"][:10]:
        typer.echo(f"  {o['planned_at']}  {o['client_id']:<34} {o['side']:<4} {o['qty']:g} {o['symbol']:<9} "
                   f"{o['tif']:<4} {o['status']}" + (f"  {o['message'][:80]}" if o["message"] else ""))


@broker_app.command("enroll")
def broker_enroll(entry_id: str, reason: str = typer.Option(..., help="Why this entry starts paper trading"),
                  confirm: str = typer.Option(..., help="Type the entry id again")) -> None:
    """Give a paper-book entry a sleeve on its broker, so its decisions become paper orders.

    Args:
        entry_id: Paper-book entry.
        reason: Why.
        confirm: The entry id again.
    """
    from .broker import execution

    sl = execution.enroll(entry_id, reason, confirm, actor="cli")
    typer.echo(f"{entry_id}: sleeve of {sl['capital']:,.0f} on {sl['broker']}")


@broker_app.command("execute")
def broker_execute() -> None:
    """Send each enrolled entry's latest decision as paper orders, within the limits. Safe to repeat."""
    from .broker import execution

    for r in execution.execute(actor="cli"):
        typer.echo(f"  [{r['status']:<14}] {r['entry_id']} {r.get('question', '')}  decision {r['as_of']}")
        for o in r["orders"]:
            typer.echo(f"      {o['side']:<4} {o['qty']:g} {o['symbol']:<9} {o['tif']:<4} {o['status']}"
                       + (f"  {o['message'][:80]}" if o["message"] else ""))
        for n in r["notes"]:
            typer.echo(f"      note: {n}")


@broker_app.command("catchup")
def broker_catchup(api: str = typer.Option("http://127.0.0.1:8765/api", help="Lab API; used when it is up")) -> None:
    """After the open: sync, then send once what each sleeve's latest rebalance left unfilled, as day orders.

    Args:
        api: Lab API address.
    """
    import httpx

    from .broker import execution
    from .paper.nightly import api_up

    if api_up(api):
        res = httpx.post(f"{api}/broker/catchup", timeout=300)
        res.raise_for_status()
        results = res.json()
    else:
        results = execution.catch_up(actor="cli")
    for r in results:
        typer.echo(f"  [{r['status']:<14}] {r['entry_id']}  decision {r['as_of']}")
        for o in r["orders"]:
            typer.echo(f"      {o['side']:<4} {o['qty']:g} {o['symbol']:<9} {o['tif']:<4} {o['status']}"
                       + (f"  {o['message'][:80]}" if o["message"] else ""))
        for n in r["notes"]:
            typer.echo(f"      note: {n}")


@broker_app.command("sync")
def broker_sync() -> None:
    """Fetch the state of open orders, store new fills and reconcile each account."""
    from .broker import execution

    out = execution.sync()
    typer.echo(f"{out['updated']} orders updated, {out['fills']} new fills")
    for name, r in out["reconciliation"].items():
        typer.echo(f"  {name}: {'reconciled' if r['ok'] else 'MISMATCH ' + json.dumps(r['mismatches'])}")
    for e in out["errors"]:
        typer.echo(f"  error: {e}")


@broker_app.command("kill")
def broker_kill(reason: str = typer.Option("kill switch", help="Why")) -> None:
    """Stop all paper trading now: no new orders, open orders cancelled, every sleeve sold.

    Args:
        reason: Why.
    """
    from .broker import execution

    out = execution.kill(reason, actor="cli")
    typer.echo(f"KILLED. {len(out['cancelled'])} open orders cancelled; "
               f"{sum(len(v) for v in out['flatten'].values())} sell orders sent. Resume: qlab broker resume")


@broker_app.command("pause")
def broker_pause(scope: str = typer.Argument("all", help="all, or an entry id"),
                 reason: str = typer.Option(..., help="Why")) -> None:
    """Stop new orders (all, or one entry) and keep positions. Takes effect at once.

    Args:
        scope: ``all`` or an entry id.
        reason: Why.
    """
    from .broker import controls

    controls.pause(scope, reason, actor="cli")
    typer.echo(f"paused {scope}")


@broker_app.command("flatten")
def broker_flatten(entry_id: str, reason: str = typer.Option(..., help="Why")) -> None:
    """Pause one sleeve and sell what it holds.

    Args:
        entry_id: Entry with a sleeve.
        reason: Why.
    """
    from .broker import execution

    out = execution.flatten(entry_id, reason, actor="cli")
    typer.echo(f"{entry_id}: paused; {len(out['orders'])} sell orders sent")


@broker_app.command("resume")
def broker_resume(scope: str = typer.Argument("all", help="all, or an entry id"),
                  reason: str = typer.Option(..., help="Why it is safe to trade again"),
                  confirm: str = typer.Option(..., help="Type RESUME")) -> None:
    """Ask to lift a kill or a pause. It takes effect 24 hours from now.

    Args:
        scope: ``all`` or an entry id.
        reason: Why.
        confirm: The word RESUME.
    """
    from .broker import controls

    row = controls.resume(scope, reason, confirm, actor="cli")
    typer.echo(f"resume of {scope} recorded; it takes effect at {row['effective_at']} UTC")


@broker_app.command("limits")
def broker_limits(
    set_: list[str] = typer.Option([], "--set", help="name=value; tightening is instant, loosening waits 24 h"),
    reason: str = typer.Option("", help="Why (required to change)"),
    confirm: str = typer.Option("", help="Type LOOSEN to loosen a limit"),
) -> None:
    """Show the limits, or change them.

    Args:
        set_: Changes as ``name=value``.
        reason: Why.
        confirm: ``LOOSEN`` when any change loosens.
    """
    from .broker import controls

    if set_:
        out = controls.set_limits(_parse_params(",".join(set_)), reason, actor="cli", confirm=confirm)
        typer.echo(f"limits recorded, in effect from {out['effective_at']}"
                   + (f" (loosened: {', '.join(out['loosened'])})" if out["loosened"] else ""))
    for k, v in controls.limits().items():
        typer.echo(f"  {k:<20} {v:g}")


inbox_app = typer.Typer(help="Research inbox: arXiv papers scored by a local model, specs, review and replication",
                        no_args_is_help=True)
app.add_typer(inbox_app, name="inbox")


@inbox_app.command("run")
def inbox_run(
    max_papers: int = typer.Option(100, help="Most papers to fetch from arXiv"),
    triage_limit: int = typer.Option(100, help="Most papers to score in this run"),
    spec_min_score: int = typer.Option(5, help="Rubric score needed for a draft spec"),
    spec_limit: int = typer.Option(3, help="Most specs to draft"),
    query: list[str] = typer.Option(None, help="A keyword query (repeatable); default: the configured ones"),
    no_queries: bool = typer.Option(False, "--no-queries", help="Fetch only the newest listing"),
    api: str = typer.Option("http://127.0.0.1:8765/api", help="Lab API; the run happens there when it is up"),
) -> None:
    """Fetch new papers, score them on the rubric, draft specs for the best, and send the digest. Exits 1 if the run
    failed.

    Args:
        max_papers: Most papers to fetch from the newest listing.
        triage_limit: Most papers to score.
        spec_min_score: Score needed for a spec.
        spec_limit: Most specs.
        query: Keyword queries to run after the listing.
        no_queries: Skip the keyword queries.
        api: Lab API address.
    """
    import time

    import httpx

    from .inbox import digest
    from .paper.nightly import api_up

    frontier = [] if no_queries else (list(query or []) or None)
    if api_up(api):
        res = httpx.post(f"{api}/inbox/run", timeout=30, json={
            "max_papers": max_papers, "triage_limit": triage_limit, "spec_min_score": spec_min_score,
            "spec_limit": spec_limit, "frontier": frontier})
        if res.status_code >= 400:
            raise typer.BadParameter(res.json().get("detail", res.text))
        job_id, job = res.json()["id"], {}
        typer.echo(f"inbox run {job_id} started on the lab server")
        while True:
            time.sleep(15)
            job = next((j for j in httpx.get(f"{api}/inbox/jobs", timeout=30).json() if j["id"] == job_id), {})
            if job.get("status") != "running":
                break
        for line in job.get("log", [])[-5:]:
            typer.echo(f"  {line}")
        for line in job.get("digest") or []:
            typer.echo(line)
        if job.get("status") != "done":
            raise typer.Exit(1)
        return
    out = digest.run(max_papers, triage_limit, spec_min_score, spec_limit, frontier, actor="cli", log=typer.echo)
    for line in out["digest"]["headline"]:
        typer.echo(line)


@inbox_app.command("papers")
def inbox_papers(min_score: int = typer.Option(3, help="Only papers scoring at least this"),
                 limit: int = typer.Option(20, help="How many")) -> None:
    """Show the best-scoring papers with their rubric dots.

    Args:
        min_score: Minimum score.
        limit: Number of papers.
    """
    from .inbox import triage

    for p in triage.latest(min_score, limit):
        dots = "".join("●" if (p["dots"] or {}).get(d) else "○" for d in triage.DOTS)
        typer.echo(f"  {p['score']}/7 {dots}  {p['arxiv_id']:<12} {p['title'][:80]}")
        if p["one_line"]:
            typer.echo(f"        {p['one_line'][:110]}")


@inbox_app.command("spec")
def inbox_spec(arxiv_id: str) -> None:
    """Draft a spec for one paper with the local model.

    Args:
        arxiv_id: Paper id, e.g. 2610.23749.
    """
    from .inbox import spec

    s = spec.draft(arxiv_id, actor="cli")
    typer.echo(f"spec {s['spec_id']} ({s['status']}): {s['spec']['template']} on {s['spec']['target']} "
               f"{s['spec']['params']} fit {s['spec']['fit']}")


@inbox_app.command("specs")
def inbox_specs(status: str = typer.Option("", help="Only this status, e.g. draft")) -> None:
    """List specs with their status and results.

    Args:
        status: Optional status filter.
    """
    from .inbox import spec

    for s in spec.specs(status or None):
        res = s["results"] or {}
        typer.echo(f"  {s['spec_id']}  {s['status']:<14} {s['spec'].get('template', ''):<20} "
                   f"{s['spec'].get('target', ''):<16} {(s['title'] or '')[:60]}"
                   + (f"  [{res.get('verdict')}]" if res else ""))


@inbox_app.command("review")
def inbox_review(spec_id: str, decision: str = typer.Argument(..., help="approve or reject"),
                 reason: str = typer.Option(..., help="Why")) -> None:
    """Approve or reject a spec.

    Args:
        spec_id: Spec id.
        decision: ``approve`` or ``reject``.
        reason: Why.
    """
    from .inbox import spec

    s = spec.review(spec_id, decision, reason, actor="cli")
    typer.echo(f"{spec_id}: {s['status']}")


@inbox_app.command("replicate")
def inbox_replicate(spec_id: str) -> None:
    """Replicate an approved spec over the paper's sample and after publication (ledger trials).

    Args:
        spec_id: Spec id.
    """
    from .inbox import replicate

    s = replicate.replicate(spec_id, actor="cli")
    r = s["results"]
    typer.echo(f"{spec_id}: {r['verdict']} (claimed {r['claimed']['claimed_sharpe']}, lab "
               f"{(r['in_sample'] or {}).get('sharpe')}); post-publication: "
               f"{(r['post_publication'] or {}).get('sharpe', (r['post_publication'] or {}).get('reason'))}")


@inbox_app.command("judge")
def inbox_judge(spec_id: str) -> None:
    """Send an approved spec through the gauntlet.

    Args:
        spec_id: Spec id.
    """
    from .inbox import replicate

    s = replicate.judge(spec_id, actor="cli")
    typer.echo(f"{spec_id}: card {s['card_id']}")


@inbox_app.command("digest")
def inbox_digest(days: int = typer.Option(7, help="Period in days")) -> None:
    """Build the inbox digest and send its headline through the alert channels.

    Args:
        days: Period.
    """
    from .inbox import digest

    for line in digest.digest(days, actor="cli")["headline"]:
        typer.echo(line)


@inbox_app.command("usage")
def inbox_usage() -> None:
    """Show the model cost meter for the last 30 days."""
    from .inbox import digest

    u = digest.usage()
    for r in u["by_provider"]:
        typer.echo(f"  {r['provider']:<7} {r['model']:<14} {r['purpose']:<7} {r['calls']} calls ({r['ok']} ok), "
                   f"{r['seconds'] / 60:.1f} min, {r['tokens_in'] + r['tokens_out']:,} tokens, {r['cost_usd']:.2f} USD")
    typer.echo(f"this month: {u['month']['cost_usd']:.2f} of {u['month']['budget_usd']:.2f} USD budget")


mine_app = typer.Typer(help="Strategy mining: budgeted searches over the grammar, every candidate a ledger trial",
                       no_args_is_help=True)
app.add_typer(mine_app, name="mine")


def _print_job(rep: dict) -> None:
    """Print a mining job's report: progress, the luck threshold it faces, and its top candidates.

    Args:
        rep: Job report.
    """
    p = rep.get("progress", {})
    typer.echo(f"job {rep['job_id']}  {rep['question']}  {rep['paradigm']}  {rep['status']}  "
               f"{p.get('n_trials', 0)} trials ({p.get('n_valid', 0)} scored)  {rep.get('message', '')}")
    if p.get("N"):
        typer.echo(f"  N = {p['N']} on the scope: luck alone reaches edge {p['sr0']:.2f}; DSR 0.95 needs about "
                   f"{p['bar']:.2f}; best found {p.get('best_edge') or float('nan'):+.2f}")
    for r in rep.get("reference", []):
        typer.echo(f"  known  {r['rule']:<20} edge {r['edge'] if r['edge'] is not None else float('nan'):+.2f}")
    for i, t in enumerate(rep.get("top", []), 1):
        o = t.get("originality") or {}
        typer.echo(f"  {i:>2}. {t['strategy']}  edge {t['edge']:+.2f} (gross {t['gross_edge']:+.2f})  DSR "
                   f"{t['dsr']:.3f}  turnover {t['turnover'] or 0:.1f}/y  "
                   f"corr {o.get('corr') if o.get('corr') is not None else float('nan'):+.2f} with {o.get('rule')}")
        typer.echo(f"      {t['text']}")
        if t.get("rationale"):
            typer.echo(f"      why: {t['rationale']}")
    for c in rep.get("cards", []):
        typer.echo(f"  card {c['card_id']}: {c['strategy']} {c['verdict']}"
                   + (f" at {c['failed_stage']}" if c.get("failed_stage") else ""))


@mine_app.command("run")
def mine_run(
    scope: str = typer.Argument(..., help="A universe (crypto_top30, all_etfs...) or an instrument (SPY, BTCUSDT)"),
    paradigm: str = typer.Option("gp", help="random, tpe, gp or llm"),
    trials: int = typer.Option(500, help="Trials this job may evaluate (capped by the question's budget)"),
    minutes: float = typer.Option(30.0, help="Wall-clock limit"),
    seed: int = typer.Option(1, help="Random seed"),
    workers: int = typer.Option(0, help="Processes; 0 uses up to 8"),
    source: str = typer.Option("", help="Data source of an instrument; guessed when empty"),
    ban: list[str] = typer.Option([], help="Operator, terminal, rule or component to leave out (repeatable)"),
    hint: str = typer.Option("", help="Research direction for the llm paradigm"),
    note: str = typer.Option("", help="Why this job"),
    api: str = typer.Option("http://127.0.0.1:8765/api", help="Lab API; the job runs there when it is up"),
) -> None:
    """Mine one scope within a budget. Every candidate is a ledger trial on mined:<scope>:1d.

    Args:
        scope: Universe or instrument.
        paradigm: Search paradigm.
        trials: Trial budget of the job.
        minutes: Time limit.
        seed: Random seed.
        workers: Evaluation processes.
        source: Data source of an instrument.
        ban: Parts of the grammar to leave out.
        hint: Research direction for the language model.
        note: Why.
        api: Lab API address.
    """
    import os

    import httpx

    from .mining import jobs
    from .paper.nightly import api_up

    n_workers = workers or min(8, max(1, (os.cpu_count() or 2) - 1))
    sc = jobs.parse_scope(scope, source)
    body = {"kind": sc.kind, "name": sc.name, "source": sc.source, "paradigm": paradigm, "max_trials": trials,
            "max_minutes": minutes, "seed": seed, "workers": n_workers, "banned": ban, "hint": hint, "note": note}
    if api_up(api):
        res = httpx.post(f"{api}/mining/jobs", json=body, timeout=30)
        if res.status_code >= 400:
            raise typer.BadParameter(res.json().get("detail", res.text))
        job_id = res.json()["job_id"]
        typer.echo(f"job {job_id} queued on the lab server; follow it with `qlab mine show {job_id}` or the Mining Lab")
        return
    row = jobs.create(sc, paradigm, trials, minutes, seed, n_workers, ban, hint, note, actor="cli")
    _print_job(jobs.run(row["job_id"], log=typer.echo))


@mine_app.command("nightly")
def mine_nightly(
    workers: int = typer.Option(0, help="Evaluation processes; 0 uses all cores but one, at most 4"),
    minutes: float = typer.Option(40.0, help="Wall-clock limit of the job"),
    api: str = typer.Option("http://127.0.0.1:8765/api", help="Lab API; the job runs there when it is up"),
) -> None:
    """Tonight's rotation job: pick the scope and paradigm, mine within budget, judge the best only past the bar.

    Args:
        workers: Evaluation processes.
        minutes: Time limit.
        api: Lab API address.
    """
    import os

    from .mining import nightly

    n = workers or min(4, max(1, (os.cpu_count() or 2) - 1))
    out = nightly.run(api, n, minutes, log=typer.echo)
    typer.echo(out["message"])
    for i, t in enumerate(out.get("top") or [], 1):
        edge = t["edge"] if t["edge"] is not None else float("nan")
        dsr = t["dsr"] if t["dsr"] is not None else float("nan")
        corr = t["corr"] if t["corr"] is not None else float("nan")
        typer.echo(f"  {i}. edge {edge:+.2f}  DSR {dsr:.3f}  corr {corr:+.2f} with {t['rule']}  {t['text']}")
    if "failed" in out["message"]:
        raise typer.Exit(1)


@mine_app.command("jobs")
def mine_jobs(limit: int = typer.Option(20, help="How many")) -> None:
    """List recent mining jobs.

    Args:
        limit: Most rows.
    """
    from .mining import jobs

    for j in jobs.jobs(limit):
        best = f"{j['best_edge']:+.2f}" if j["best_edge"] is not None else "  –"
        typer.echo(f"{j['job_id']}  {j['created_at'][:16]}  {j['question']:<28} {j['paradigm']:<6} {j['status']:<8} "
                   f"{j['n_trials'] or 0:>5} trials  best {best}")


@mine_app.command("show")
def mine_show(job_id: str) -> None:
    """Show a mining job's report.

    Args:
        job_id: Job id.
    """
    from .mining import jobs

    _print_job(jobs.load(job_id))


@mine_app.command("stop")
def mine_stop(job_id: str, api: str = typer.Option("http://127.0.0.1:8765/api", help="Lab API")) -> None:
    """Stop a job running on the lab server after its current batch.

    Args:
        job_id: Job id.
        api: Lab API address.
    """
    import httpx

    res = httpx.post(f"{api}/mining/jobs/{job_id}/stop", timeout=30)
    typer.echo(res.json().get("detail", "stop requested") if res.status_code >= 400 else "stop requested")


@mine_app.command("gauntlet")
def mine_gauntlet(
    job_id: str,
    strategy: str = typer.Argument("", help="A mined_... name from the job; empty sends the best"),
    seed: int = typer.Option(7, help="Gauntlet seed"),
    api: str = typer.Option("http://127.0.0.1:8765/api", help="Lab API; the run happens there when it is up"),
) -> None:
    """Send a mined candidate to the gauntlet, judged like any other card.

    Args:
        job_id: Job id.
        strategy: Candidate name; empty sends the job's best.
        seed: Gauntlet seed.
        api: Lab API address.
    """
    import httpx

    from .mining import jobs
    from .paper.nightly import api_up

    name = strategy or jobs.load(job_id)["top"][0]["strategy"]
    if api_up(api):
        res = httpx.post(f"{api}/mining/jobs/{job_id}/gauntlet", json={"strategy": name, "seed": seed}, timeout=900)
        if res.status_code >= 400:
            raise typer.BadParameter(res.json().get("detail", res.text))
        card = res.json()
    else:
        card = jobs.send_to_gauntlet(job_id, name, seed, actor="cli")
    _print_card(card)


@mine_app.command("budget")
def mine_budget(
    question: str = typer.Argument("", help="mined:<scope>:1d; empty lists every mining question"),
    trials: int = typer.Option(-1, help="New cap on the question's mining trials"),
    reason: str = typer.Option("", help="Why (required to change)"),
) -> None:
    """Show mining budgets, or set one question's cap.

    Args:
        question: Mining question.
        trials: New cap; -1 only shows.
        reason: Why.
    """
    from .mining import jobs

    if question and trials >= 0:
        jobs.set_budget(question, trials, reason, actor="cli")
    rows = [jobs.budget_of(question)] if question else jobs.budgets()
    for b in rows:
        typer.echo(f"{b['question']:<30} {b['used']:>6} of {b['max_trials']:>6} trials used  ({b['reason']})")


@mine_app.command("propose")
def mine_propose(
    scope: str = typer.Argument(..., help="Universe or instrument"),
    n: int = typer.Option(5, help="Proposals"),
    source: str = typer.Option("", help="Data source of an instrument"),
    hint: str = typer.Option("", help="Research direction"),
) -> None:
    """Ask the local model for strategy ideas with rationales, validated against the grammar. Runs no backtest.

    Args:
        scope: Universe or instrument.
        n: Proposals wanted.
        source: Data source of an instrument.
        hint: Research direction.
    """
    from .mining import grammar, jobs
    from .mining.evaluate import Evaluator
    from .mining.llm import Proposer, describe_scope

    sc = jobs.parse_scope(scope, source)
    space = Evaluator.for_scope(sc, leak_cuts=0).space
    prop = Proposer(describe_scope(sc, space), hint=hint)
    for spec, why in prop.propose(space, n):
        typer.echo(f"- {grammar.describe(spec)}\n  why: {why}")
    for idea in prop.outside:
        typer.echo(f"- (outside the grammar) {idea}")
    typer.echo(f"{prop.rejected} proposals rejected by the grammar, {prop.calls} model calls")


@mine_app.command("truth")
def mine_truth(report_id: str, workers: int = typer.Option(0, help="Processes; 0 uses all cores but one")) -> None:
    """Measure the true edges of a mining calibration's admitted noise runs on long fresh noise markets.

    Args:
        report_id: Mining calibration report id.
        workers: Processes.
    """
    from .mining import calibration as mc

    for r in mc.add_truth(report_id, workers or None, log=typer.echo):
        typer.echo(f"  {r['family']:<18} seed {r['seed']}: edge {r['edge']:+.2f} in the study, true edge "
                   f"{r['true_edge']:+.2f} ({', '.join(f'{e:+.2f}' for e in r['true_edges'])})  {r['text']}")


@app.command("calibrate-mining")
def calibrate_mining(
    seed: int = typer.Option(400_000, help="Base seed; confirm a choice with a different one"),
    workers: int = typer.Option(0, help="Worker processes; 0 uses all cores but one"),
    scale: float = typer.Option(1.0, help="Multiply every market count (0.1 for a quick look)"),
    budget_scale: float = typer.Option(1.0, help="Multiply every search budget"),
    paradigms: str = typer.Option("random,tpe,gp", help="Comma-separated paradigms"),
    markets: str = typer.Option("bars,panel,xs30", help="Comma-separated market types"),
) -> None:
    """Calibrate the gauntlet at mining scale: miners search synthetic noise and planted markets, submit their best.

    Args:
        seed: Base seed for the simulated markets.
        workers: Worker processes.
        scale: Factor on the market counts; a count above 0 keeps at least 1.
        budget_scale: Factor on the trials per search.
        paradigms: Paradigms to compare.
        markets: Market types to include.
    """
    from .mining import calibration as mc

    keep = [m.strip() for m in markets.split(",") if m.strip()]

    def scaled(d: dict, f: float) -> dict:
        return {k: (max(1, round(v * f)) if v and k in keep else 0) for k, v in d.items()}

    rep = mc.run_study(seed, tuple(p.strip() for p in paradigms.split(",") if p.strip()),
                       {k: max(10, round(v * budget_scale)) for k, v in mc.BUDGET.items()},
                       scaled(mc.NOISE_REPS, scale), scaled(mc.POWER_REPS, scale), workers=workers or None,
                       log=typer.echo, actor="cli")
    typer.echo(f"\nReport {rep['id']} ({rep['elapsed_s']} s, {rep['settings']['n_trials']:,} trials recorded)")
    for r in sorted(rep["noise"], key=lambda r: r["family"]):
        typer.echo(f"  noise  {r['family']:<20} {r['admitted']:>3}/{r['n']:<4} {r['rate']:6.1%}  "
                   f"[{r['low']:.1%}, {r['high']:.1%}]")
    for r in sorted(rep["power"], key=lambda r: (r["family"], r["true_edge"] or 0)):
        typer.echo(f"  power  {r['family']:<20} edge {r['true_edge']:.2f}  {r['admitted']:>3}/{r['n']:<4} "
                   f"{r['rate']:6.1%}")
    for b in rep["benchmark"]:
        typer.echo(f"  {b['scenario']:<12} {b['family']:<20} best in-sample {b['best_edge'] or float('nan'):+.2f} "
                   f"vs luck {b['sr0'] or float('nan'):.2f}; formulas {b['formula_share'] or 0:.0%}; "
                   f"failed {b['failed']}")
    typer.echo(rep["headline"])


if __name__ == "__main__":
    app()


radar_app = typer.Typer(help="Pipeline D (M10): the radar: news, attention, crowd forecasts and market stress, watched "
                             "for anomalies", no_args_is_help=True)
app.add_typer(radar_app, name="radar")
RADAR_API = "http://127.0.0.1:8765/api"


def _radar_call(api: str, method: str, path: str, body: dict | None = None, timeout: float = 3600) -> dict | None:
    """Call the Lab API's radar endpoints when the API is up.

    Args:
        api: Lab API address.
        method: ``get`` or ``post``.
        path: Path below ``/api``.
        body: JSON body for a post.
        timeout: Seconds to wait (a deep pass can take many minutes when GDELT is slow).

    Returns:
        The decoded answer, or None when the API is down (the caller runs in-process).
    """
    import httpx

    from .paper.nightly import api_up

    if not api_up(api):
        return None
    res = httpx.request(method.upper(), f"{api}{path}", json=body, timeout=timeout)
    res.raise_for_status()
    return res.json()


def _print_collect(out: dict) -> None:
    """Print a collection pass and exit 1 when every source failed.

    Args:
        out: Output of ``radar.collect.run``.

    Raises:
        typer.Exit: Every source failed.
    """
    if out.get("skipped"):
        typer.echo("skipped: another radar pass is still running")
        return
    for s in out["sources"]:
        typer.echo(f"  {'ok ' if s['ok'] else 'ERR'} {s['source']:<8} {s['message'][:200]}")
    if out["sources"] and not any(s["ok"] for s in out["sources"]):
        raise typer.Exit(1)


def _print_scan(out: dict) -> None:
    """Print a scan's summary.

    Args:
        out: Output of ``radar.scan.run``.
    """
    if out.get("skipped"):
        typer.echo("scan skipped: another radar scan is still running")
        return
    o = out["open"]
    typer.echo(f"scan {out['run_id']}: {out['n_signals']} signals, {o['severe']} severe and {o['notable']} notable "
               f"anomalies open; opened {len(out['opened'])}, closed {len(out['closed'])}")
    for a in out["alerts"]:
        typer.echo(f"  alert: {a}")
    if out.get("paused"):
        typer.echo("  new paper orders PAUSED by the radar (QLAB_RADAR_AUTOPAUSE)")


@radar_app.command("collect")
def radar_collect(deep: bool = typer.Option(False, "--deep", help="The daily pass: history, Wikipedia and GDELT"),
                  only: str = typer.Option("", help="Comma-separated sources: wiki, gdelt, hn, markets, stress"),
                  api: str = typer.Option(RADAR_API, help="Lab API; the pass runs there when it is up")) -> None:
    """Collect from the radar's sources: the quick pass, or the deep daily one.

    Args:
        deep: Run the deep pass.
        only: Limit to these sources.
        api: Lab API address.
    """
    body = {"kind": "deep" if deep else "quick", "only": [s.strip() for s in only.split(",") if s.strip()]}
    out = _radar_call(api, "post", "/radar/collect", body)
    if out is None:
        from .radar import collect

        out = collect.run(body["kind"], body["only"] or None, log=typer.echo, actor="cli")
    _print_collect(out)


@radar_app.command("scan")
def radar_scan(recalibrate: bool = typer.Option(False, help="Recalibrate every detector first"),
               api: str = typer.Option(RADAR_API, help="Lab API; the scan runs there when it is up")) -> None:
    """Run every detector on every signal, record anomalies and raise the radar's alerts.

    Args:
        recalibrate: Recalibrate thresholds before scanning.
        api: Lab API address.
    """
    out = _radar_call(api, "post", "/radar/scan", {"recalibrate": recalibrate})
    if out is None:
        from .radar import scan

        out = scan.run(recalibrate=recalibrate, log=typer.echo, actor="cli")
    _print_scan(out)


@radar_app.command("run")
def radar_run(deep: bool = typer.Option(False, "--deep", help="The daily pass instead of the quick one"),
              recalibrate: bool = typer.Option(False, help="Recalibrate every detector before scanning"),
              api: str = typer.Option(RADAR_API, help="Lab API; the pass runs there when it is up")) -> None:
    """Collect, then scan: what the server's qlab-radar timers run.

    Args:
        deep: Run the deep pass.
        recalibrate: Recalibrate thresholds.
        api: Lab API address.
    """
    body = {"kind": "deep" if deep else "quick", "only": [], "recalibrate": recalibrate}
    out = _radar_call(api, "post", "/radar/run", body)
    if out is None:
        from .radar import collect, scan

        out = {"collect": collect.run(body["kind"], log=typer.echo, actor="cli"),
               "scan": scan.run(recalibrate=recalibrate, log=typer.echo, actor="cli")}
    _print_scan(out["scan"])
    _print_collect(out["collect"])


@radar_app.command("show")
def radar_show(api: str = typer.Option(RADAR_API, help="Lab API to read when it is up")) -> None:
    """Print the open anomalies, the detectors' measured false-alarm rates and each source's status.

    Args:
        api: Lab API address.
    """
    o = _radar_call(api, "get", "/radar", timeout=120)
    if o is None:
        from .radar import overview

        o = overview.overview()
    s = o["scan"]
    typer.echo("no scan yet" if s is None else
               f"last scan {s['created_at']} UTC: {s['n_signals']} signals, {s['open']['severe']} severe and "
               f"{s['open']['notable']} notable open; automatic pause {'ON' if o['autopause'] else 'off'}")
    for a in o["anomalies"]:
        typer.echo(f"  [{a['severity']:<7}] {a['label']} ({a['detector']}, since {str(a['started'])[:10]})")
    cal = o.get("calibration")
    if cal:
        typer.echo(f"calibration {cal['calib_id']} ({cal['created_at']}): false alarms per month on null histories")
        for d, v in cal["summary"].items():
            if v.get("null_rate") is not None:
                typer.echo(f"  {d:<9} {v['signals']:>3} signals: notable {v['null_rate']:.2f}, severe "
                           f"{v['null_rate_severe']:.3f}; planted 3-sigma step caught {v['detect_rate']:.0%}")
    for src in o["sources"]:
        typer.echo(f"  source {src['source']:<8} {src.get('status') or '-':<6} {src.get('series', 0):>3} series, "
                   f"newest published {str(src.get('last_published') or '-')[:16]}  {(src.get('message') or '')[:90]}")


@radar_app.command("scenarios")
def radar_scenarios(search: str = typer.Option("", help="Search open Polymarket events to add to the watchlist"),
                    api: str = typer.Option(RADAR_API, help="Lab API to read when it is up")) -> None:
    """Print the scenario board, or search Polymarket for new questions.

    Args:
        search: Words to search for (prints matching events instead of the board).
        api: Lab API address.
    """
    from .radar import scenarios

    if search:
        for e in scenarios.search(search):
            typer.echo(f"{e['slug']}  {e['title']}  (ends {e['end']}, volume {e['volume'] or 0:,.0f})")
            for m in e["markets"][:6]:
                p = "–" if m["prob"] is None else f"{m['prob']:.0%}"
                typer.echo(f"    {p:>5}  {m['label']}")
        return
    b = _radar_call(api, "get", "/radar/scenarios", timeout=60) or scenarios.board()
    typer.echo(b["note"])
    for sc in b["scenarios"]:
        typer.echo(f"\n{sc['title']} [{sc['source']}]" + (f"  base rate: {sc['base_rate']}" if sc["base_rate"] else ""))
        for m in sc["markets"]:
            ch = "" if m["change_7d"] is None else f"  ({m['change_7d'] * 100:+.0f} pts in 7 days)"
            typer.echo(f"    {m['prob']:>5.0%}  {m['label']}{ch}")
        if not sc["markets"]:
            typer.echo("    (not collected yet)")
