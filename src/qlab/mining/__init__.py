"""Strategy mining (M5): a grammar of signals, filters, exits and sizing, searched within budgets.

Every candidate a search evaluates is a ledger trial on the scope's mining question, so the Deflated Sharpe of
anything later tested on that scope counts it. Mining reads research data only, compiles candidates only through
the grammar (nothing generated is executed), and hands its best to the unchanged gauntlet. Decision 0011.

Modules: ``formula`` (the formula language), ``grammar`` (specs, validation, compilation), ``evaluate`` (scoring on
research data), ``search`` (random, Bayesian, genetic programming, LLM proposals), ``llm`` (the proposer), ``jobs``
(budgets, jobs, reports, the gauntlet hand-off) and ``calibration`` (the miner researcher on synthetic markets).
"""
