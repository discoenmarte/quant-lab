"""Plain-language definitions shown next to every metric in the UI."""

GLOSSARY: dict[str, dict[str, str]] = {
    "sharpe": {
        "term": "Sharpe ratio",
        "short": "Return per unit of risk, per year. Above 1 is very good; most strategies are below 0.5.",
        "long": "Average excess return divided by its volatility, annualised. It doesn't care whether the "
        "volatility is up or down, and one lucky backtest can make it look great, so always read it with "
        "the trial count N.",
    },
    "cagr": {
        "term": "CAGR",
        "short": "Compound annual growth rate: the steady yearly rate that gets you from start to end.",
        "long": "If $100 became $200 in 10 years, CAGR is about 7.2%.",
    },
    "max_dd": {
        "term": "Maximum drawdown",
        "short": "The worst fall from a previous peak, in percent.",
        "long": "The pain number. A strategy that once fell 50% needs +100% to recover.",
    },
    "sortino": {
        "term": "Sortino ratio",
        "short": "Like Sharpe, but only counts downside volatility as risk.",
        "long": "Useful for strategies with asymmetric returns.",
    },
    "calmar": {
        "term": "Calmar ratio",
        "short": "CAGR divided by maximum drawdown.",
        "long": "How much yearly growth you get per unit of worst-case pain.",
    },
    "turnover": {
        "term": "Turnover",
        "short": "How much of the portfolio is traded per year. 2.0 means the whole book twice a year.",
        "long": "Turnover times cost per trade is roughly what trading costs eat from returns.",
    },
    "exposure": {
        "term": "Exposure",
        "short": "Fraction of the time the strategy holds a position.",
        "long": "A trend rule that is invested 65% of the time earns cash (the risk-free rate) the rest.",
    },
    "n_trials": {
        "term": "Trial count (N)",
        "short": "How many distinct backtests were run on this instrument before the candidate was picked.",
        "long": "Test 5,000 worthless strategies and dozens will look excellent by chance. The lab records "
        "every trial, in every strategy family, so the significance bar can rise with N.",
    },
    "edge_sharpe": {
        "term": "Edge Sharpe (appraisal ratio)",
        "short": "Sharpe of what the strategy earns beyond simply holding less of the market.",
        "long": "The return left after removing a fixed position in buy-and-hold. A rule that is 60% invested "
        "in a rising market scores zero here. Positive exactly when adding the strategy to buy-and-hold "
        "improves the best mix. The gauntlet judges this, not the raw Sharpe.",
    },
    "fdr": {
        "term": "False-discovery chance",
        "short": "Probability that a strategy this good has no real edge, given how rare real edges are.",
        "long": "Bayes' rule with a prior share of real ideas (5% by default) and the size of a real edge "
        "(Sharpe 0.5). López de Prado, Lipton & Zoonekynd (2026).",
    },
    "mintrl": {
        "term": "Minimum track record",
        "short": "How many years of returns it takes before this Sharpe is distinguishable from zero.",
        "long": "Shorter for high Sharpe ratios, longer with fat tails and autocorrelation.",
    },
    "calibration": {
        "term": "Calibration",
        "short": "Running the gauntlet on fake markets where the truth is known, to measure its error rates.",
        "long": "Noise markets give the false-positive rate (how often luck passes); markets with a planted "
        "trend give the power (how often a real edge is caught) for each history length.",
    },
    "breakeven": {
        "term": "Break-even cost",
        "short": "The cost per trade, in basis points, at which the edge would vanish.",
        "long": "Gross edge divided by turnover. Far above the modelled cost is safe; close to it is fragile.",
    },
    "psr": {
        "term": "Probabilistic Sharpe Ratio (PSR)",
        "short": "Probability that the true Sharpe is above zero, given how much data you have.",
        "long": "Bailey & López de Prado. Accounts for track length, skew and fat tails.",
    },
    "dsr": {
        "term": "Deflated Sharpe Ratio (DSR)",
        "short": "PSR, but against the best Sharpe you'd expect from luck after N trials.",
        "long": "The lab's main gate. A DSR of 0.95 means a 95% confidence that the edge beats the best "
        "result luck would produce after the trials already run on this instrument. That best result grows with "
        "how widely the trials' Sharpes spread. From thresholds v3 the spread counts only what luck can produce, "
        "so a rule that loses its costs every day doesn't raise the bar for the others.",
    },
    "pbo": {
        "term": "Probability of Backtest Overfitting (PBO)",
        "short": "How often the in-sample winner falls below the median out of sample.",
        "long": "Computed with combinatorially symmetric cross-validation (CSCV). Low is good; 0.5 means the "
        "selection process is no better than a coin.",
    },
    "permutation": {
        "term": "Permutation test",
        "short": "Re-runs the strategy on shuffled data with the same returns but no patterns.",
        "long": "If the real result isn't clearly better than the shuffled ones, the strategy is "
        "finding noise.",
    },
    "bootstrap": {
        "term": "Bootstrap Monte Carlo",
        "short": "Resamples blocks of the strategy's own returns to show the range of possible futures.",
        "long": "Used for risk (drawdown ranges), not as proof of an edge.",
    },
    "plateau": {
        "term": "Plateau test",
        "short": "A real edge survives small changes to its settings; a lucky one dies one step away.",
        "long": "Share of neighbouring parameter settings that also work. The lab requires at least 0.6.",
    },
    "walk_forward": {
        "term": "Walk-forward test",
        "short": "Choose settings on the past, test them on the next unseen period, repeat.",
        "long": "The closest a backtest gets to how the strategy would actually have been run.",
    },
    "lockbox": {
        "term": "Lockbox",
        "short": "Data from a fixed date on (2024-10-01), kept untouched and used once at the very end.",
        "long": "Spent per research question the first time anything looks at it, including a Backtest Studio "
        "run that reaches past the date. After that, only forward paper trading is independent evidence.",
    },
    "lookahead": {
        "term": "Look-ahead bias",
        "short": "Using information that wasn't available at the time of the decision.",
        "long": "The silent killer of backtests. The lab tests for it by changing future data and checking "
        "that past decisions don't move.",
    },
    "survivorship": {
        "term": "Survivorship bias",
        "short": "Testing only on assets that still exist today, which hides the ones that failed.",
        "long": "Free stock data usually has it, so the lab started with ETFs and crypto majors. The crypto "
        "cross-section avoids it differently: Binance's archive keeps delisted pairs (LUNA, ANC, BTT...), and each "
        "day's universe is chosen from data up to that day.",
    },
    "slippage": {
        "term": "Slippage and spread",
        "short": "The gap between the price you expected and the price you got.",
        "long": "Backtests that ignore it overstate returns, sometimes completely.",
    },
    "regime": {
        "term": "Regime",
        "short": "A market state, such as bull, bear, calm or volatile.",
        "long": "A robust strategy shouldn't depend on one regime that may not come back.",
    },
    "paper_book": {
        "term": "Forward paper book",
        "short": "Each day's targets, recorded before the next bar exists, then scored as bars arrive.",
        "long": "The only evidence no backtest can fake: the decisions are timestamped before their outcomes. "
        "Backfills are refused. The band shows where the forward record should be if the research result is "
        "true (90% of the time inside it).",
    },
    "forward_edge": {
        "term": "Forward edge Sharpe",
        "short": "Edge Sharpe on the days after each decision, with the research period's beta held fixed.",
        "long": "Compared with the card's research edge Sharpe plus or minus 1.645 standard errors. With a few "
        "months of data the band is very wide; an edge Sharpe of 0.3 needs decades to tell from zero.",
    },
    "cusum": {
        "term": "CUSUM decay alarm",
        "short": "Adds up, day by day, the evidence that the edge has fallen from its research level to zero.",
        "long": "Page's cumulative-sum test. It fires when the sum crosses a threshold set for one false alarm "
        "every ten years while the edge is as researched. It catches breaks and large decay quickly and small "
        "decay only after years.",
    },
    "lead_time": {
        "term": "Decision lead time",
        "short": "Hours between recording a decision and the open it fills at.",
        "long": "Positive is the honest case: decided after the close, before the next open. A decision "
        "recorded after the open is kept but marked late, because filling it at that open is optimistic.",
    },
    "point_in_time": {
        "term": "Point-in-time universe",
        "short": "The instruments a strategy could have chosen from on each date, using only what was known then.",
        "long": "The crypto universes are the top 30 or 100 pairs by dollar volume over the past 30 days, "
        "recomputed every day. A coin enters after 30 days of trading and leaves when it falls out of the top N "
        "or stops trading.",
    },
    "availability_mask": {
        "term": "Availability mask",
        "short": "Which instrument has a bar on which date, in a panel where instruments list and delist.",
        "long": "Prices are blank where an instrument doesn't trade. A position in an instrument with no bar the "
        "next day is sold at its last close, which is optimistic: real delistings come with falling prices "
        "and wider spreads.",
    },
    "drifting_positions": {
        "term": "Drifting positions",
        "short": "Between rebalances each holding grows or shrinks with its own price, as in a real account.",
        "long": "The crypto cross-section is simulated this way. Holding constant fractions would mean "
        "rebalancing for free every day, a hidden daily trade that is worth several percent a year when coins "
        "move 5% a day.",
    },
    "lifecycle": {
        "term": "Lifecycle state",
        "short": "Where a report card is now: candidate, validated, in the paper book, near-miss, rejected or retired.",
        "long": "Derived from the card's verdict, the paper book and the owner's retirements, never typed in. The "
        "verdict is shown next to it, so a rejected card that is tracked in the paper book still reads rejected.",
    },
    "near_miss": {
        "term": "Near-miss",
        "short": "Rejected only by the luck stages: the permutation test or the Deflated Sharpe.",
        "long": "It passed every check of how the rule behaves (costs, out of sample, neighbours, regimes) but its "
        "edge was measured too imprecisely to tell from luck. Worth free forward days in the paper book, not money. "
        "Near is about which stages failed, not by how much: read its p-value and DSR.",
    },
    "daily_report": {
        "term": "Daily lab report",
        "short": "Since the last report: what the lab tested, what passed, what is in the paper book, how it is doing.",
        "long": "Built from the ledger, the report cards, the paper book's saved scores, the audit log and the data "
        "catalog after every nightly job (or on demand). It runs no backtest, so building it adds no trial.",
    },
    "freshness": {
        "term": "Data freshness",
        "short": "How many sessions a source's stalest default symbol is behind the latest finished session.",
        "long": "Tolerances absorb what the calendar doesn't model: 1 session for US ETFs (holidays), 2 days for "
        "Binance (the archive publishes a day late), 95 days for the Ken French factors (published monthly with a "
        "lag). Beyond them a stale-data alert fires.",
    },
    "alert_rules": {
        "term": "Alert rules",
        "short": "A new candidate, a paper-book decay alarm, stale data, a failed nightly job.",
        "long": "Each condition alerts once, in the first report that sees it, and again only if it clears and comes "
        "back; a failed nightly job alerts every time. A forward edge outside its 90% band is not an alert, because "
        "that happens one day in ten by design.",
    },
}
