"""Pipeline D, the radar: news, attention, crowd forecasts and market stress, watched for anomalies.

The radar is a risk-and-awareness tool first (decision 0016). Published work finds that attention and news sentiment
mostly predict volatility and trading volume, weakly predict returns, and that what they predict decays once it is
known. So the radar's outputs are facts with a measured rarity (what moved, how unusual, since when) and responses
that only ever reduce risk. Any trading use of a radar series is a hypothesis like any other: pre-registered, judged
by the gauntlet, counted in the ledger.

- ``store``: Parquet series with ``published_at`` and ``fetched_at`` on every row, a catalog, and a polite cached
  fetcher (downloads under ``<data>/raw/radar``).
- ``watchlist``: what is watched (queries, articles, keywords, scenarios, stress series) and the pre-committed
  response for each category. ``<data>/radar/watchlist.json`` overrides the defaults.
- ``sources``: one collector per free source.
- ``sentiment``: a small cited headline lexicon (the Loughran-McDonald lists are free for academic research only).
- ``detect``: robust z-scores, CUSUM and Bayesian online change points, with thresholds set from history for a
  stated false-alarm budget.
- ``scan``: the detectors on every watched signal, the alert rule, and the opt-in automatic pause.
- ``scenarios``: the scenario board of prediction-market probabilities.
- ``research``: the pre-registered attention and tone rules for the gauntlet, and their synthetic calibration markets.
"""
