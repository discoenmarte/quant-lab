"""Command line entry point: ``qlab serve | init | ingest | backtest | portfolio | gauntlet | portfolio-gauntlet |
calibrate | bench | demo | paper``.

More commands arrive with each milestone (inbox, mine).
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
) -> None:
    """Measure the gauntlet's false-positive rate on noise and its power on planted edges.

    Args:
        noise_reps: Noise runs per family and researcher type.
        power_reps: Planted-edge runs per setting.
        workers: Worker processes.
        seed: Base seed for the simulated markets.
        portfolio_reps: Noise runs per portfolio family and researcher type.
    """
    from .gauntlet.calibration import run_calibration

    rep = run_calibration(noise_reps, power_reps, workers=workers or None, log=typer.echo, actor="cli",
                          base_seed=seed, portfolio_reps=portfolio_reps)
    typer.echo(f"\nReport {rep['id']} ({rep['elapsed_s']} s, {rep['settings']['n_trials']:,} trials recorded)")
    for r in rep["noise"]:
        typer.echo(f"  noise  {r['family']:<14} {r['scenario']:<12} {r['admitted']:>3}/{r['n']:<4} "
                   f"{r['rate']:6.1%}  [{r['low']:.1%}, {r['high']:.1%}]")
    for r in rep["power"]:
        typer.echo(f"  power  {r['family']:<14} edge {r['true_edge']:.2f}  {r['years']:>4g} y  "
                   f"{r['admitted']:>3}/{r['n']:<4} {r['rate']:6.1%}")
    typer.echo(rep["headline"])


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
def paper_nightly(api: str = typer.Option("http://127.0.0.1:8765/api", help="Lab API to use when it is up")) -> None:
    """Ingest the free sources, decide every active entry, and score the book. Exits 1 if a step failed.

    Uses the running Lab API when it answers (it holds the database), otherwise runs everything here.

    Args:
        api: Base URL of the Lab API.
    """
    from .paper.nightly import nightly
    from .timeutil import now_utc

    typer.echo(f"== {now_utc().isoformat(timespec='seconds')}")
    res = nightly(api, log=typer.echo)
    for r in res["ingest"]:
        typer.echo(f"  ingest {r['source']:<8} {r['status']} ({r['n_series']} series)")
    for r in res["run"]:
        typer.echo(f"  run    [{r['status']:<8}] {r['question']:<34} {r['message']}")
    for r in res["score"]:
        typer.echo(f"  score  [{'ALARM' if r.get('alarm') else r['status']:<8}] {r['question']:<34} {r['message']}")
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


if __name__ == "__main__":
    app()
