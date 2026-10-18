"""Command line entry point: ``qlab serve | init | ingest | backtest | gauntlet | calibrate | bench | demo``.

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
) -> None:
    """Download free data, store it as Parquet and run the quality checks.

    Args:
        source: Which source to ingest.
        symbols: Optional symbol filter.
    """
    from .data.ingest import SOURCES
    from .data.ingest import ingest as run

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

    card = run_gauntlet(source, symbol, interval, strategy, _parse_params(params), costs, seed, actor="cli")
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
) -> None:
    """Measure the gauntlet's false-positive rate on noise and its power on planted edges.

    Args:
        noise_reps: Noise runs per family and researcher type.
        power_reps: Planted-edge runs per setting.
        workers: Worker processes.
    """
    from .gauntlet.calibration import run_calibration

    rep = run_calibration(noise_reps, power_reps, workers=workers or None, log=typer.echo, actor="cli")
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


if __name__ == "__main__":
    app()
