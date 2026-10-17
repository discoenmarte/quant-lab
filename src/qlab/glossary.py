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
        "short": "How many backtests were run on this research question. More tries, more luck.",
        "long": "Test 5,000 worthless strategies and dozens will look excellent by chance. The lab records "
        "every trial so the significance bar can rise with N.",
    },
    "psr": {
        "term": "Probabilistic Sharpe Ratio (PSR)",
        "short": "Probability that the true Sharpe is above zero, given how much data you have.",
        "long": "Bailey & López de Prado. Accounts for track length, skew and fat tails.",
    },
    "dsr": {
        "term": "Deflated Sharpe Ratio (DSR)",
        "short": "PSR, but against the best Sharpe you'd expect from luck after N trials.",
        "long": "The lab's main gate. A DSR of 0.95 means a 95% chance the edge is real after accounting for "
        "how many things were tried.",
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
        "short": "The most recent data, kept untouched and used once at the very end.",
        "long": "Once opened for a strategy family it's burned; reusing it turns it into ordinary "
        "in-sample data.",
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
        "long": "Free stock data usually has it. The lab starts with ETFs and crypto majors for that reason.",
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
}
