# 0005. Portfolios and forward evidence before the paper inbox and mining

- Date: 2026-10-18
- Status: accepted

## Context

The roadmap's order after the gauntlet was M4 (LLM paper inbox), M5 (strategy mining), M6 (registry and
portfolio), M7 (paper trading). Three findings from M3 and from current practice argue for a different order
when the goal is the most profit for the least cost.

1. **Power on one instrument is low.** Calibration (decision 0004) shows that even an honest, well-tuned gauntlet
   catches a true edge Sharpe of 0.5 only about 35% of the time with 10 years of daily data and about 60% with 20.
   That is the arithmetic of `t = SR·√years`, not a flaw in the gates. Independent edges of Sharpe `s` on `k`
   instruments combine to roughly `s·√k` (Grinold's fundamental law), which is both more return per unit of risk
   and far easier to verify.
2. **Diversified, published rules are what works in practice.** Managed-futures trend across many markets has a
   century of evidence (Hurst, Ooi & Pedersen, 2017) and had a strong 2026 (the SG CTA index was up about 15.7%
   through Q3 2026, after losing more than 11% in the first five months of 2025: it is lumpy). Cross-asset
   momentum, trend filters and risk parity are the low-cost, capacity-unconstrained core of systematic investing.
   Crypto funding-rate carry, once the easiest retail edge, has compressed to mid-single digits in 2026.
3. **Mining and LLM-generated strategies have the weakest evidence.** FactorBench (2026) found no mining paradigm
   that consistently dominates after costs; every independent evaluation of LLM-discovered strategies in 2025-2026
   found no persistent edge. They cost money (LLM calls) and raise N, which raises the bar for everything else.

And one constraint no code can remove: **forward evidence accrues only with calendar time.** The go-live
checklist needs 3-6 months of paper trading. Every week the forward clock isn't running is a week added to the
earliest live date.

## Decision

Reorder the milestones:

| Order | Milestone | Why now |
|---|---|---|
| M3 | Validation gauntlet | Done. |
| **M3b** | **Portfolio lab:** aligned multi-asset panels, a weights engine with the same timing and costs, the classic diversified strategies (time-series momentum, trend filter, cross-sectional momentum, risk parity, volatility management), and the same gauntlet and calibration for portfolios | Most expected profit per unit of work; best statistical power. |
| **M7a** | **Forward paper book:** each day, record target weights with a timestamp before the next bar exists, then score them as data arrives. No broker needed. | Starts the forward clock at zero cost. |
| M6 | Registry, alerts | Needed once several candidates are incubating. |
| M7b | Broker paper trading (Alpaca paper, exchange testnets) | Execution realism: fills, slippage, reconciliation. |
| M4 | Research inbox | Feeds the gauntlet with published ideas once the portfolio core exists. |
| M5 | Mining | Last: only with a budget, and judged against the portfolio it would join. |
| M8, M9 | Go-live gate, intraday | Unchanged. |

## Consequences

- The cheapest, best-evidenced strategies get judged first, by the same calibrated gauntlet.
- LLM spend is deferred, so the lab stays at $0 a month until M4.
- Portfolio candidates are judged against equal weight of the same instruments run through the same engine (an
  implementable passive alternative), not against cash.
- Volatility-targeting rules have a real edge in markets with volatility clustering, so their false-positive rate
  can't be calibrated on GARCH noise. Only the timing families are calibrated that way; volatility rules rely on
  the gauntlet's other stages and on forward evidence.
