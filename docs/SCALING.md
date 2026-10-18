# Scaling path: what to build next, what it costs, what it can earn

*Written 2026-10-18, after M3 and the first portfolio verdicts. Prices are the roadmap's October 2026 figures
(ROADMAP §8); check them before paying for anything. Nothing here is investment advice. It is arithmetic about
this lab, so you can decide where your time and money go.*

## 1. Where the lab stands

| | |
|---|---|
| Monthly cost today | **$0** (your Mac, free data, open-source tools) |
| What works | Data lake (21 ETFs, BTC/ETH, factors), single-asset and portfolio engines with costs, a ten-stage gauntlet, calibration on synthetic markets, a ledger of 1.6M trials (almost all from calibration), a web console |
| How honest the gauntlet is | Lets noise through **1.0-1.4%** of the time in two independent studies (no family above 3%); catches an edge Sharpe of **1.0 95%** of the time and **0.5 about half** the time with 20 years of daily data |
| What it found on real data | **Nothing passed.** Ten pre-registered portfolio candidates (five published rules on two universes) all rejected. Best near-miss: the ten-month trend filter, edge Sharpe 0.34, permutation p ≈ 0.05, false-discovery chance ~55%, losing ~6% in 2008 against ~36-42% for equal weight |

The second-to-last line is the important one. The lab didn't fail; it measured something true. **A realistic
edge (Sharpe 0.2-0.4 beyond buy-and-hold) on ~20 years of daily data can't be told apart from luck**, by any
honest method. The minimum track record for an edge Sharpe of 0.34 at 95% confidence is about 23 years.

## 2. The lever that matters: breadth

Statistical power and profit both scale with the number of *independent* bets, not with cleverness:

- Grinold's fundamental law: information ratio ≈ skill per bet × √(independent bets per year).
- `t ≈ edge Sharpe × √years`. You can't buy more years, but you can buy more independent instruments.

| Path | Instruments | Data cost | What it unlocks | Main risk |
|---|---|---|---|---|
| **Crypto cross-section** | 100-300 coins on Binance, **including delisted ones** (the archive keeps them) | **$0** | Cross-sectional momentum, reversal, size and volume factors (Liu, Tsyvinski & Wu, 2022) with real breadth | Survivorship and liquidity: needs point-in-time universes (top N by trailing volume) |
| **US stocks** | 3,000-8,000 stocks, survivorship-free | **≈ $35-55/month** (Norgate; Sharadar similar) | Classic factor research (momentum, quality, low volatility, value) with huge breadth; the Open Source Asset Pricing library becomes testable | Factor decay after publication (McLean & Pontiff: about −58%); short-selling costs |
| Futures (the CTA universe) | 50-100 markets | ≈ $50-150/month, plus futures margin | What managed-futures funds actually trade: real diversification for trend | Data and execution cost, contract rolls |
| Intraday (M9) | Same instruments, many more bets | ≈ $29-99/month minute data | More observations per year | Costs and competition grow fast; the 2026 evidence on retail intraday is poor |

**Recommendation:** build the crypto cross-section next (milestone **M3c**). It is free, it uses code that exists
(panels, engine, gauntlet, calibration), and it is the cheapest way to get the breadth that daily ETF timing lacks.
It needs one design change: panels with instruments that enter and leave (an availability mask) instead of the
current "start when everyone exists". Spec in `docs/HANDOFF.md` §6.

Pay for stock data only when one of these is true: the crypto cross-section has produced a candidate (the
pipeline works end to end), or you are doing this for the skills and career value, which is real.

## 3. What it can earn: honest arithmetic

Excess return over cash ≈ Sharpe × volatility. A diversified book run at 10% volatility with a net Sharpe of 0.5
(after costs and some post-publication decay) earns about **5% a year above cash**, with drawdowns of 15-25% along
the way. Equal-weight diversified ETFs alone (no timing) have earned roughly that in 2007-2024; the timing layer is
mainly drawdown protection, not extra return.

| Capital | 5% above cash per year | 2% extra from a validated edge | Lean lab ($16/mo) | Comfortable lab ($75/mo) | Stock data ($50/mo) |
|---|---|---|---|---|---|
| $5,000 | $250 | $100 | covered | not covered | not covered |
| $25,000 | $1,250 | $500 | covered | covered | covered by the edge alone? barely |
| $100,000 | $5,000 | $2,000 | covered | covered | covered |
| $1,000,000 | $50,000 | $20,000 | covered | covered | covered |

Read the "2% extra" column as the value of the research itself: what the lab adds over simply holding a
diversified portfolio. **A paid upgrade is worth it when (expected extra return) × (capital) exceeds its yearly
cost.** At a 2% edge, $50/month of data ($600/year) needs about $30,000 of capital to pay for itself, before
taxes. Most of the lab's value below that size is learning, a portfolio piece, and protection from bad strategies.

Costs that eat returns, in order of size:

1. **Taxes.** Strategies that rebalance monthly realise short-term gains; in many tax systems that can cost more
   than all trading costs. Tax-advantaged accounts change the arithmetic. (Rules depend on where you live.)
2. **Behaviour.** Abandoning a strategy in a drawdown. The bootstrap stage's size recommendation exists for this.
3. **Trading costs.** Small for ETFs (2-3 bps a side at zero-commission brokers); about 0.1% a side for crypto
   spot. The gauntlet already charges them.
4. **Infrastructure.** $0-75/month. The smallest item.

## 4. The path, step by step

| Step | What | Monthly cost | Gate to move on |
|---|---|---|---|
| Now | M3c crypto cross-section on free data; calibrate dynamic universes | $0 | A candidate passes the gauntlet, or the study says crypto factors don't survive costs either |
| Next | M7a forward paper book: each night record target weights before the next bar exists, score later. Every candidate and near-miss goes in | $0 (Mac + launchd) or $5/month VPS for reliability | Runs unattended for 30 days |
| Then | M6 registry and alerts, M7b broker paper trading (Alpaca paper, Binance testnet: free) | $5-20 | 3-6 months of forward results inside the backtest's band |
| Optional | Stock data for factor breadth | +$35-55 | The capital or career case above |
| Later | M4 paper inbox (LLM), M5 mining | +$6-50 (LLM) | Only once the portfolio core is producing candidates; every mined trial raises N |
| Live (M8) | Tiny allocation, hard limits outside any model, kill switch | trading costs only | The go-live checklist in ROADMAP §7, all green |

Compute stays on the Mac through M5 (decision 0001: thousands of backtests a second on 15 cores). Rust or a cloud
pool are only worth it if a measurement says so; a calibration study takes about 8 minutes today.

## 5. Ways to earn from the lab without risking capital

- **WorldQuant BRAIN:** free alpha research platform with a paid consultant track. The lab's discipline (ledger,
  deflation, calibration) is exactly what makes submitted alphas survive their out-of-sample checks.
- **Numerai:** stake on predictions for a hedge fund; pays for signals that stay good out of sample.
- **Career:** an honest, calibrated research lab with a written decision trail is a strong portfolio piece for
  quant and data roles. Statistically, this is the most reliable return the lab offers today.

## 6. What would change this plan

- A gauntlet-validated candidate with a forward record inside its band: move capital-related steps forward.
- Calibration showing the gauntlet too strict for dynamic universes: retune on synthetic data first, never on the
  real candidates.
- Real-money results outside the backtest band (decay alarm): stop, per the go-live checklist.
