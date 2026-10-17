# 0001. Python first, Rust where a measurement says so

- Date: 2026-10-17
- Status: accepted

## Context

The question was whether parts of the lab should be written in Rust for speed.

Nothing in the planned lab is latency-critical. Daily strategies decide once a day. The intraday mode (M9)
decides once a minute. Milliseconds matter only for market making and high-frequency trading, which the lab
deliberately avoids: that is where retail systems compete with co-located firms and lose.

What can be slow is throughput: mining (M5) and intraday backtests (M9) may run thousands of backtests. The
heavy lifting already runs in compiled code (NumPy, DuckDB, Arrow), as long as our own code is vectorised.

## Decision

1. Write the lab in Python, vectorised wherever possible.
2. Measure. M2 ships a benchmark of the backtest engine on daily and minute bars.
3. Move a piece to Rust (PyO3 + maturin) only when profiling shows it dominates run time, it can't be
   vectorised, and Rust gives at least a 10x speed-up on the real workload.
4. For order execution (M7), use NautilusTrader, which has a Rust core and a Python API, instead of writing
   our own engine.

## Likely candidates, if any

- Path-dependent backtests on minute bars (stops, targets, trailing exits) in M9.
- Evaluation of mined formula alphas over many symbols in M5.

## Consequences

One language to learn and maintain for now; CI and setup stay simple. The cost is that a hot loop might be
slower than it could be until it's measured and moved.
