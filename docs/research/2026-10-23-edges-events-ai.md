# Edges, events and AI: what quant-lab can honestly hunt for, and how to be ready for 2027

*Research report, dated 2026-10-23. Sources were read on 2026-10-22. Owner: discoenmarte.*

This report answers six questions the owner asked: where durable edges still exist for an individual systematic
trader, what sentiment and social media really predict, how to forecast and survive surprises, what the AI picture
looks like going into 2027, what respected practitioners and online communities are saying, and what quant-lab is
missing. It ends with ranked recommendations, a design brief for the event radar (`qlab.radar`) that a parallel
session is building, a glossary, and the full source list.

Nothing here is investment advice. It is evidence about strategies, for a research lab that places no real-money
orders.

## How to read this

Every claim carries a tag:

- **[V]** verified: I read the primary source myself (the paper, the authors' own blog, official data or a filing).
  "abstract" means only the abstract or summary page was readable.
- **[S]** secondary: news coverage, an aggregator, a search-engine summary, or a summary of a primary source I could
  not open.
- Confidence: **H** high (official data, or several independent sources agree), **M** medium (one good source, or
  contested), **L** low (one secondary source, a short sample, or self-reported numbers).

Numbers are quoted as the sources give them. Where I did arithmetic myself, the text says so. Pages that could not be
read are listed in [section 8](#8-what-could-not-be-read).

---

## 0. Summary

### The ten most important findings

1. **Published anomalies are mostly gone for anyone outside microcaps.** About 200 published long-short equity
   anomalies earned a median 7 bp a month after 2005 in non-micro stocks, and modest allowances for costs or luck
   remove even that ([Chen & Welch 2026](https://arxiv.org/abs/2607.06502) [V abstract, H]). The same decay shows up
   in index options (no significant alpha after about 2012), the overnight futures drift (about zero since 2021), the
   pre-FOMC drift (gone after 2015), the S&P 500 inclusion effect (under 1%), large-cap earnings drift (dead since
   2016) and firm-level insider buying (mostly 2008-09). Price patterns on liquid instruments are the most crowded
   space there is, and the lab's 22 rejections fit that picture.
2. **What survives is compensation for risk, or for absorbing flows that big players must make.** Trend following
   has a century of out-of-sample evidence and a strong 2026 (SG Trend about +14.6% to 22 October, after +2.4% in
   2025). Crypto carry earned a Sharpe of 6.45 in 2020-25 but has compressed hard: 4.06 from
   2024 and negative in 2025. Calendar rebalancing by pension and target-date funds moves next-day equity returns by
   17 bp and costs those funds about $16bn a year.
3. **Realistic edges are Sharpe 0.2-0.5 and only become useful in combination.** The lab's own calibration catches a
   single 0.5 edge about half the time with 20 years of data, and its paper book needs about 21 years before a 0.33
   edge's forward band excludes zero. A pre-registered portfolio of premia, judged as one unit, is the missing
   research track.
4. **Costs and execution decide most outcomes at retail scale.** The lab's intraday rules die at 28 bps round trips;
   the one lead (ETH range breakout, gross edge Sharpe 1.24) breaks even at 12 bps a side against 14 charged.
   Exchange perpetual futures charge a fraction of spot fees, but maker (limit-order) execution brings adverse
   selection: one documented Polymarket market-making bot saw its fill rate fall from 37% to 1% in four months.
5. **Sentiment and attention predict trading volume and volatility more reliably than returns.** The return effects
   that exist are short-lived, concentrated in small stocks and in negative news, and they fade as more people use
   them (LLM-headline returns fall as LLM adoption rises). The most famous result, "Twitter mood predicts the
   market", did not replicate.
6. **LLM trading results are mostly artefacts or noise so far.** Backtests are contaminated by look-ahead unless the
   model's training cutoff is before the test period. Independent live records show no directional edge (two DX
   fleets, thousands of agents; the 17-day Alpha Arena), and a single day of manipulated headlines cut an LLM
   system's annual return by up to 17.7 percentage points.
7. **Prediction markets forecast well, but their trading edges are thin.** Kalshi beat the Bloomberg consensus on
   headline CPI and had a perfect day-before record on FOMC decisions (Federal Reserve staff paper). On Polymarket,
   contracts priced at 90 cents or more earned only 0.83 cents per dollar before fees; longshots lost 19.3 cents. They
   are excellent radar inputs and poor trading venues for this lab.
8. **Crises are predictable as base rates, not as dates.** Rapid credit and asset-price growth over three years
   precedes a financial crisis within three years about 40% of the time, against 7% normally. An industry that beat
   the market by 100% in two years had about a 53% chance of a 40% crash, yet no reliably lower average return. Trend
   following and volatility targeting are the cheapest robust defences; buying puts is cheaper than before 2012 but
   still a drag over long periods.
9. **The AI picture is two-sided.** Capability measures keep compounding (METR's public frontier at about 12 hours of
   human-equivalent task length, frontier training compute growing about 5x a year), and big-tech capex is heading
   for roughly $700-760bn in 2026 against $413bn in 2025, with the top ten S&P 500 stocks at about 37-39% of the
   index. Measured productivity gains are still modest (+4-20% in METR's trial), and labour effects so far fall on
   young workers in exposed jobs (19% below trend). 2026 already brought a war-driven oil shock and a Fed hike to
   3.75-4%. Forecasters' medians for transformative AI sit between 2028 and 2033. A capex bust and an acceleration are
   both plausible in 2027.
10. **Robustness beats prediction.** The lab can't time an AI bust or boom. It can hold return sources that worked in
    different regimes, size positions by volatility, detect breaks with a measured false-alarm rate, decide in
    advance what it does when a detector fires, and keep running when a data source or an exchange fails.

### Ranked recommendations

Costs in money are monthly unless stated; time is focused development time. Expected value (EV) is a judgement
backed by the evidence column, with arithmetic shown in [section 6](#6-critique-of-quant-lab-and-recommendations).

| # | Recommendation | Money / time | EV | Main risk | Evidence |
|---|---|---|---|---|---|
| 1 | **Portfolio-of-premia track**: pre-register 5-8 sleeves (multi-asset trend, crypto trend, carry, the two near-misses, a flow sleeve), fixed equal-risk weights, judged as one portfolio against the owner's real alternative, with evidence-class priors | $0 / 2-3 weeks | Highest | Sleeves correlate in a crisis; combination weights get fitted | Lab calibration (0004, 0007); Longmore; Carver; AQR; Hurst-Ooi-Pedersen |
| 2 | **Crypto perpetuals and funding**: ingest the free Binance futures archive (funding rates, premium index, open interest); a carry sleeve, funding as a crash-risk signal, perp fees in the cost model | $0 / 1-2 weeks | High-medium (carry compressed since 2024) | Exchange failure, auto-deleveraging, stablecoin depeg, jurisdiction | BIS WP 1087; Borri et al.; He et al.; FTI on 10 Oct 2025 |
| 3 | **Radar as a risk overlay**: regime detectors with a false-alarm budget, a pre-registered volatility-targeting and de-risking rule for the paper book, and scenario stress tests | $0 / 2-4 weeks | Medium (drawdown, not return) | False alarms that cost returns; overfitting the detectors | Harvey et al. 2018; Cederburg et al. 2020; GHSS 2022; Adams & MacKay |
| 4 | **Execution study**: measure maker fills and adverse selection on paper and testnet, then a new pre-registered cheaper-execution question for the ETH breakout | $0 / 1-2 weeks | Medium | Optimistic fill assumptions | Decision 0015; kacho.io; Barbon et al. |
| 5 | **Flow and calendar sleeve**: month-end rebalancing reversal (SPY/TLT) and the shifted turn of month, pre-registered, low turnover | $0 / 1 week | Small but cheap | Decay as others front-run | Harvey-Mazzoleni-Melone; Longmore; Quanter Lab |
| 6 | **Futures data for real trend breadth** (50-100 markets), only once capital or career value justifies it | $50-150 / 2-4 weeks | High if capital allows | Data cost, margin, rolls | Carver (214 instruments); Hurst-Ooi-Pedersen; SCALING.md |
| 7 | **Refocus mining** away from price-only grammar on crowded instruments, toward new inputs (funding, open interest, flows); cap N | $0 / days | Protects future verdicts | Lost optionality (small) | Chen & Welch; Gençay; FactorBench (decision 0011) |
| 8 | **Gauntlet diagnostics**: a decay check (sub-period and change-point on the research-period edge), portfolio-level judging, and the gross-plateau calibration study from decision 0015 | $0 / 1-3 weeks | Medium | A threshold change that isn't calibrated | PEAD, overnight, pre-FOMC deaths; Jacquier et al. via Aligrithm |
| 9 | **Sentiment and LLM research aimed at volatility, not returns**, with point-in-time model vintages, anonymised text and an adversarial-text filter | $0-20 / 2-3 weeks | Low for returns, medium for sizing | Look-ahead, manipulation | Tetlock; Antweiler & Frank; Glasserman & Lin; Rizvani et al. |
| 10 | **Prediction markets as data, not as a venue**: a historical calibration study net of fees; event probabilities feed the radar | $0 / 1 week | Low (trading), medium (radar) | Legal access, insider trading on venues | Diercks-Katz-Wright; Cardozo & Rivero-Wildemauwe |
| 11 | **Housekeeping**: deploy the server, finish the SEC insider run (expect rejection), then stop work on insiders | €8 / days | Operational | None material | Donttrade; Knewtson et al. 2010 |

**Don't do:** systematic put buying in a small account, taker-priced intraday trading, copy-trading wallets, letting
an LLM agent trade, or chasing a strategy because it worked in the last year.

---

## 1. Where durable edges are in 2026

The question is narrow: an individual with $10k-$1M, free or cheap data, retail execution. The table first, then the
evidence for each line.

| Edge | Evidence quality | Size after costs today | Capacity for $10k-$1M | Data cost | Verdict for the lab |
|---|---|---|---|---|---|
| Published equity anomalies (non-micro) | Very strong, negative | ~0 | n/a | $35-55 (stock data) | Don't build on it |
| Trend following, many markets | Very strong, positive long run, lumpy | Positive long run (SG Trend 5.3%/yr since inception), large year-to-year swings | Fine; breadth limited by capital and data | $0 (ETFs, crypto) to $50-150 (futures) | Core sleeve |
| Crypto carry (funding, basis) | Strong 2020-24, compressed since | Unclear in 2025-26; funding baseline near 11%/yr before costs (my arithmetic) | Fine | $0 | Sleeve plus risk signal |
| Index volatility risk premium | Strong before ~2012, insignificant after | ~0 alpha | Options cost money | $$ | Don't sell vol for "alpha" |
| Tail hedging with puts | Strong: costs money long run | Negative carry | Small accounts pay the most | $$ | Prefer trend + volatility targeting |
| Rebalancing flows (month end) | Good (one NBER paper, practitioner replication) | Small (a 17 bp next-day effect in the paper) | Fine | $0 | Small sleeve |
| Turn of month, pre-FOMC, overnight drift, index inclusion | Decayed after publication | ~0 | n/a | $0 | Don't build on it |
| Leveraged-ETF and options-hedging flows at the close | Good but intraday and fading | Small, needs intraday data | Fine | $29-99 | Not now |
| Crypto cross-sectional momentum, CTREND | Good, but overfit risk and high turnover | Positive gross, cost-sensitive | Fine on liquid coins | $0 | Tested; costs bind |
| Prediction markets (favourite-longshot) | Mixed | Under 1 cent per dollar before fees | Small | $0 | Research only |
| Small-scale market making | Practitioner evidence negative | Erodes quickly | Small | $0-$$ | Not for this lab |
| Insider buying after 2008 | Weak | ~0 | Fine | $0 | Finish the test, then stop |
| LLM and AI trading agents | Independent evidence negative | ~0 or negative | n/a | $ | Use AI for labour, not as the edge |

### 1.1 Anomaly decay: the base rate is efficiency

- **The 21st century killed most published equity anomalies.** Median zero-investment returns across ~200
  anomalies from the Open Source Asset Pricing library: 48 bp a month before 2006 in all stocks, 19 bp after 2005,
  26 bp in non-micro stocks, and **7 bp after 2005 in non-micro stocks**; modest allowances for luck or costs
  eliminate the 7 bp ([Chen & Welch, *What Useful Alphas?*, arXiv 2607.06502, Jul 2026](https://arxiv.org/abs/2607.06502)
  [V abstract, H]).
- **Publication itself destroys returns.** Across 97 predictors, returns were 26% lower out of sample and 58% lower
  after publication ([McLean & Pontiff, *JF* 2016](https://ideas.repec.org/a/bla/jfinan/v71y2016i1p5-32.html) [S, H]).
- **Replication is contested, but magnitudes shrink either way.** Of 452 anomalies, 65% fail |t| ≥ 1.96 once
  microcaps are controlled, 82% at a multiple-testing hurdle of 2.78, and those that replicate are smaller than
  reported ([Hou, Xue & Zhang, *RFS* 2020](https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2019-2133..html) [V abstract,
  H]). A Bayesian reading finds most factors do replicate, in 13 themes and 93 countries ([Jensen, Kelly & Pedersen,
  *JF* 2023](https://ideas.repec.org/a/bla/jfinan/v78y2023i5p2465-2518.html) [V abstract, H]). Both can be true: the
  factors exist historically, and their post-2005 returns in tradable stocks are close to zero.
- **The decline is broad, not equity-specific.** Value and momentum on Ken French's site had roughly zero cumulative
  returns 2002-2025, and currency carry has not outperformed since the financial crisis ([Dew-Becker & Giglio,
  Jun 2026](https://www.dew-becker.org/documents/option_decline.pdf) [V, H]).

**For the lab:** buying survivorship-free stock data to test published anomalies would mostly confirm this. The
money is better spent on breadth in premia (section 1.2-1.3).

### 1.2 Trend following and managed futures, 2020-2026

- **The long run.** A time-series momentum strategy built back to 1880 was consistently profitable over the following
  110 years ([Hurst, Ooi & Pedersen, *A Century of Evidence on Trend-Following Investing*, AQR/JPM 2017](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing)
  [V abstract, H]). Since its inception the SG Trend Index has compounded 5.33% a year with a worst drawdown of 20.6%,
  against 7.98% and 51.0% for the S&P 500 total return ([TTU, Dec 2025 report](https://www.toptradersunplugged.com/trend-following-performance-report-december-2025/)
  [V, H]).
- **2025 was weak.** SG Trend was down 9.3% after April and still −7.6% in August, then finished +2.39% (BTOP50
  +2.81%, TTU index +1.78%) while the S&P 500 returned +17.89% ([TTU Dec 2025](https://www.toptradersunplugged.com/trend-following-performance-report-december-2025/)
  [V, H]; mid-year figures [S, M] from TTU's April and August 2025 reports via search).
- **2026 is strong, driven by the oil and rates shocks.** SG Trend was +10.4% at the end of May, +11.1% at the end of
  August (S&P 500 TR +13.1%) ([TTU May 2026](https://www.toptradersunplugged.com/trend-following-performance-report-may-2026/),
  [TTU Aug 2026](https://www.toptradersunplugged.com/trend-following-performance-report-august-2026/) [V, H]) and an
  estimated +14.57% year to date on 22 October ([BarclayHedge SG Trend page](https://portal.barclayhedge.com/cgi-bin/indices/displayHfIndex.cgi?indexCat=SG-Prime-Services-Indices&indexName=SG-Trend-Index)
  [V, H]). The broader SG CTA Index was reported at +15.7% for the nine months to September, against 11.7% for the
  S&P 500, credited to a long crude position from January (before the Iran war) and positioning for rising rates
  ([CNBC, 6 Oct 2026](https://www.cnbc.com/2026/10/06/trend-following-hedge-funds-beat-stock-market.html), blocked;
  via [TokenPost](https://www.tokenpost.com/news/investing/26960) [S, M]).
- **Theory.** Trend rules profit when long-horizon autocorrelation is positive, even if short horizons mean-revert;
  their positive skew is structural; and all trend systems are strongly correlated with each other, so the choice of
  filter matters less than breadth and cost control ([Sepp & Lucic, arXiv 2607.19497, Jul 2026](https://arxiv.org/abs/2607.19497)
  [V abstract, H]).
- **Breadth is the practitioner's lever.** Rob Carver's research uses 214 futures instruments. In a July 2026 test
  of pooling forecast weights (50 random instruments, 20 years in, 1 year out), the median out-of-sample Sharpe was
  0.571 unpooled and 1.127 pooled by asset class ([qoppac.blogspot.com, 3 Jul 2026](https://qoppac.blogspot.com/)
  [V, M]: one test, costs not yet included).

**For the lab:** trend is the one premium with long out-of-sample evidence, crisis behaviour (section 3.4) and a
strong 2026. Its weakness is lumpiness: a single sleeve can lose for two or three years. Both paper-book entries are
trend filters on ETFs; the next step is more breadth (crypto trend alongside, futures later), not a cleverer filter.

### 1.3 Carry, including crypto funding

- **Crypto carry was large and is compressing.** The futures-spot basis of BTC and ETH averaged above 10% a year and
  reached up to 60%. It is driven by a convenience yield from small investors chasing trends with leverage, and
  limited by scarce arbitrage capital, because margins spike and positions get liquidated in drawdowns. High carry
  predicts crashes and moves with the price of crash insurance ([Schmeling, Schrimpf & Todorov, *Crypto carry*, BIS
  WP 1087](https://www.bis.org/publ/work1087.htm) [V abstract, H]).
- **Recent performance.** On Binance 8-hour data from August 2020 to May 2025, crypto carry had an annualised Sharpe
  of 6.45, mostly from funding (mean about 8% a year, volatility 0.8%). From 2024 the Sharpe fell to 4.06, and in 2025
  it turned negative ([Borri, Liu, Tsyvinski & Wu, arXiv 2510.14435 v4, Mar 2026](https://arxiv.org/html/2510.14435)
  [V, H]).
- **Why it compressed.** Funding was positive more than 92% of the time in Q3 2025 but clustered at the 0.01% per
  8 hours baseline, because billions of arbitrage dollars (Ethena among them) short any rich premium within hours
  ([BitMEX Q3 2025 derivatives report](https://www.bitmex.com/blog/2025q3-derivatives-report) [S, M]). My arithmetic:
  0.01% three times a day is about 10.95% a year before costs, against the opportunity cost of holding the spot
  collateral. Ethena's funding-backed stablecoin was about $14bn in September 2025 ([Borri et al.](https://arxiv.org/html/2510.14435)
  [V, M]).
- **Arbitrage in theory.** Perpetual futures deviate from no-arbitrage prices more than currency forwards do, and the
  implied arbitrage has high Sharpe ratios ([He, Manela, Ross & von Wachter, arXiv 2212.06888 v7, Sep 2026](https://arxiv.org/abs/2212.06888)
  [V abstract, H]).
- **The tail risk is real.** On 10-11 October 2025 more than $19bn of crypto leverage was liquidated in about a day.
  Funding had risen from about 10% to nearly 30% annualised by 6 October; BTC top-of-book depth fell by more than
  90%; auto-deleveraging closed some well-hedged shorts; and Ethena's USDe traded in the mid-$0.60s on Binance
  while staying near $1 elsewhere, so margin systems priced on one venue liquidated accounts that were solvent
  elsewhere ([FTI Consulting](https://fticonsulting.com/insights/articles/crypto-crash-october-2025-leverage-met-liquidity)
  [V, H]). Hyperliquid alone reportedly processed about $10bn of the liquidations
  ([The Defiant](https://thedefiant.io/newsletter/defi-daily/the-ultimate-10-10-crash-autopsy) [S, L]).
- **The data is free.** Binance's public archive holds monthly funding-rate files for USD-M perpetuals (BTCUSDT from
  2020-01 to 2026-09, with checksums) for nearly a thousand symbols (the listing returned 989 folders), plus daily open-interest and long/short metrics,
  premium-index klines, order-book depth snapshots and a BVOL options-volatility index
  ([data.binance.vision](https://data.binance.vision/), checked through its S3 listing [V, H]).
- **Fees.** Binance's base USD-M futures tier is reported at 0.02% maker and 0.05% taker
  ([TradersUnion](https://tradersunion.com/brokers/crypto/view/binance/futures-fees/) [S, M]; the official fee page did
  not render), against the lab's 10 bps taker fee in its spot crypto presets (decision 0006).

**For the lab:** carry is a different return source from trend and was the best-documented crypto premium. Its
recent compression lowers the expected return; its value now is diversification plus information (funding and basis
extremes are crash-risk signals). Whether derivatives on Binance are legally available to the owner depends on where
he lives; I did not verify this, and it must be checked before any design work.

### 1.4 The volatility risk premium and the tail-hedging debate

- **The famous premium faded after 2012.** S&P 500 options earned sharply negative returns and CAPM alphas for
  buyers in the original studies; after about 2012, over a sample as long as those studies, standard option
  strategies have no statistically significant alphas or information ratios. The break coincides with dealers' net
  positions moving from short to neutral and lower hedging frictions. The VIX, once about 3 points above realised
  volatility on average, is now close to an unbiased forecast of it ([Dew-Becker & Giglio, *The decline of the S&P
  500 variance risk premium*, Jun 2026](https://www.dew-becker.org/documents/option_decline.pdf) [V, H]).
- **Puts cost money over long periods; trend is the cheaper hedge.** From 1985 to March 2020 a rolling 5%
  out-of-the-money one-month put lost money persistently, while multi-asset trend made money. In tail events, puts
  were profitable in 7 of 8 slow drawdowns and 10 of 10 fast crash months; trend in 6 of 8 and 9 of 10. An index of
  tail-hedge funds lost about 2% a year, and about 8% a year in the 2010s. AQR concludes it prefers trend on cost
  ([Ilmanen, Thapar, Tummala & Villalon, *Tail Risk Hedging: Contrasting Put and Trend Strategies*, JSI 2021](https://www.aqr.com/insights/research/white-papers/tail-risk-hedging-contrasting-put-and-trend-strategies)
  [V, H]).
- **The Universa side.** Universa argues that a small, very convex hedge (3.33% in its strategy, 96.67% in the S&P
  500) raises a portfolio's compound growth, and reported a 3,612% return in March 2020
  ([Advisor Perspectives, 2020](https://www.advisorperspectives.com/articles/2020/05/06/an-aqr-warning-and-a-3-612-return-fire-up-the-black-swan-debate)
  [S, M]) and a reported 100% in April 2025 ([Hedgeweek, citing Reuters](https://www.hedgeweek.com/black-swan-hedge-fund-universa-up-100-amid-april-volatility-says-allocator/)
  [S, L]: one investor's account; Spitznagel declined to confirm). These are firm-reported and not comparable with
  AQR's option baskets.

**For the lab:** selling volatility is no longer paid as alpha in index options, and buying it is cheaper than it was
(my inference from Dew-Becker & Giglio: zero alpha means puts no longer lose money beyond their beta). For a small
account, trend plus volatility targeting gives most of the crisis protection without option data or option bid-ask
costs. A rules-based put sleeve is a later, paper-only test.

### 1.5 Calendar and flow effects

| Effect | What it was | What it is now | Source |
|---|---|---|---|
| Overnight drift in equity futures (2-3 a.m. New York) | ~3.7%/yr 1998-2020, over 60% of the E-mini's 5.9%/yr | ~0 from Jan 2021 to Dec 2025; closing order imbalances became smaller and less dispersed | [Boyarchenko, Larsen & Whelan, NY Fed, Jul 2026](https://libertystreeteconomics.newyorkfed.org/2026/07/the-disappearing-overnight-drift/) [V, H] |
| Pre-FOMC drift | Large excess returns before FOMC days, 1994-2011 | "Essentially disappeared after 2015" | [Kurov, Wolfe & Gilbert, *FRL* 2021](https://ideas.repec.org/a/eee/finlet/v40y2021ics1544612320315956.html) [V abstract, H] |
| S&P 500 inclusion | +7.4% for additions in the 1990s | Under 1% in the last decade; deletions ~0.1% (2010-20) | [Greenwood & Sammon, *JF* 2025](https://ideas.repec.org/a/bla/jfinan/v80y2025i2p657-698.html) [V abstract, H] |
| Turn of the month (SPY) | Last day + first three days of a month | The famous window earns 0.057%/day vs 0.049% average; the four days before the month's last day earn 0.095%/day; in locked 2012-2026 tests no rule beat holding SPY | [Quanter Lab](https://quanterlab.com/research/in-search-of-the-market-s-payday) [V, M] |
| Pension and target-date rebalancing | Calendar and threshold rebalancing by large funds | When stocks are overweight, next-day equity returns fall 17 bp; front-running costs funds ~$16bn/yr | [Harvey, Mazzoleni & Melone, NBER w33554, rev. Jan 2026](https://www.nber.org/papers/w33554) [V abstract, H] |
| SPY/TLT month-end reversal | Buy the month-to-date laggard around the 15th trading day, hold to month end | Similar in 2007-16 and 2017-25 by the author's charts; one observation a month; partly reverse-engineered from a bank note | [Longmore, Robot Wealth](https://edgealchemy.robotwealth.com/p/how-wealth-managers-pay-you-to-trade) [V, M] |
| Leveraged-ETF and options-hedging flows | End-of-day momentum from LETF rebalancing and negative dealer gamma, reverting at the next open | LETF effects have decreased over time; gamma effects persist | [Barbon, Beckmeyer, Buraschi & Moerke, SSRN 3925725](https://papers.ssrn.com/abstract=3925725) [S, M] |
| Overnight vs daytime sector-ETF rules | Reversal rules beat buy-and-hold, 1999-2025 | Outperformance "decreases significantly" at 1-2 bp costs | [MDPI *Risks* 14(4):84, 2026](https://www.mdpi.com/2227-9091/14/4/84) [V abstract, M] |

**For the lab:** the effects tied to a famous paper are gone; the ones tied to an institution that can't change its
behaviour (calendar rebalancing) are alive but small. A flow sleeve is worth one week and a pre-registration, as a
member of the premia portfolio, not as a standalone strategy.

### 1.6 Crypto: momentum, CTREND, funding and liquidation cascades

- **Short-horizon momentum survives in the published cross-section.** Two-week momentum (CMOM) earned 2.6% a week
  long-short (t = 3.89) over the full sample and 2.1% (t = 3.70) after 2020; 12- and 24-week momentum were not
  significant ([Borri et al.](https://arxiv.org/html/2510.14435) [V, H]). Weekly long-short with small coins is
  expensive to trade and hard to short.
- **CTREND is real in the published sample, and its published number is the friendly one.** The JFQA paper combines
  28 technical indicators by elastic net over 3,000+ coins and reports that the factor survives costs and holds in
  large, liquid coins ([Fieberg, Liedtke, Poddig, Walker & Zaremba, *JFQA* 60(7), 2025](https://www.cambridge.org/core/product/4C1509ACBA33D5DCAF0AC24379148178)
  [V abstract, H]). An independent replication finds the published Sharpe of 1.94 sits at the top of a 55,296-design
  grid whose median is 1.34 (1.19 with a validation slice); most of the alpha comes from the short leg; turnover is
  about 68% a week ([Aligrithm](https://aligrithm.com/a-trend-factor-for-the-crypto-cross-section-and-its-overfit-tell/)
  [V, M]).
- **The lab's own record agrees.** Eight pre-registered crypto factor candidates on point-in-time top-30 and top-100
  universes were all rejected after 20 bps-a-side costs; the long legs don't pass (decision 0006).
- **Liquidation cascades** are the main crypto tail (section 1.3). They are predictable as states (high funding, high
  open interest, unified margin) rather than as dates.

**For the lab:** the remaining crypto edges are short-horizon, short-leg heavy and cost-sensitive. Trend on liquid
coins and carry are more promising for a long-only, retail-cost account than cross-sectional factor rules.

### 1.7 Prediction markets

- **Longshot overpricing is large; favourite underpricing is small.** Across 588 million Polymarket trades by 2.48
  million accounts, purchases below 10 cents lost 19.3 cents per dollar, and purchases at 90 cents or more earned 0.83
  cents per dollar. The pattern holds in crypto and politics and is absent in sports
  ([Cardozo & Rivero-Wildemauwe, arXiv 2609.12878, Sep 2026](https://arxiv.org/abs/2609.12878) [V abstract, H]). On
  Kalshi, low-priced contracts win far less often than break-even and high-priced ones yield small positive returns,
  more so for takers ([Bürgi, Deng & Whelan, CESifo WP 12122](https://www.ifo.de/DocDL/cesifo1_wp12122.pdf) [S, M]).
- **The bias moves.** In Polymarket temperature markets, favourites are overpriced in the last 1-12 hours by about an
  order of magnitude more than longshots are at long horizons, and simple recalibration gives no significant gain
  ([Qiu & Qiu, arXiv 2610.13593, Oct 2026](https://arxiv.org/abs/2610.13593) [V abstract, L]: a five-page student
  symposium paper).
- **Fees eat the favourite edge.** Kalshi's default taker fee is reported as 0.07 × contracts × P × (1 − P), rounded up
  to the cent, so a 90-cent contract pays about 0.63 cents ([Allium](https://allium.so/blog/kalshi-fees-why-the-cost-peaks-near-50-cents/)
  [S, M]). My arithmetic: that is about 0.7% of the stake, against Polymarket's 0.83% gross favourite return. Fees
  differ by venue and series.
- **Insiders trade there.** One Polymarket account turned $30,000 into $436,760 on Maduro's exit; Kalshi found and
  reported insider traders; both venues added bans in March 2026 as senators proposed a "Prediction Markets are
  Gambling Act" ([Matt Levine, 25 Feb 2026](https://www.bloomberg.com/opinion/newsletters/2026-02-25/kalshi-found-some-insider-traders),
  [Boston Globe, 24 Mar 2026](https://www.bostonglobe.com/2026/03/24/business/kalshi-polymarket-bans-insider-trading-senators-prediction-markets)
  [S, M]). An outsider is the natural counterparty of these traders.

**For the lab:** the useful product of prediction markets is their probabilities (section 3.2), not their trades.

### 1.8 Small-scale market making

- **The best public record I found is negative.** A Polymarket esports and sports bot (January to April 2026, $95,830
  of volume, 3,858 bets) netted +$4,973: arbitrage legs +$8,293, unplanned directional exposure −$3,185. Monthly
  profit fell from about $2,506 in February to $390 in March; its fill rate fell from 37.4% in January to 1% in
  April, as faster bots took the top of the book and picked off quotes priced from odds up to 30 minutes stale
  ([kacho.io, real numbers](https://kacho.io/polymarket-arbitrage-real-numbers),
  [why it lost money](https://kacho.io/why-my-polymarket-arbitrage-bot-lost-money) [V, M]: self-reported, one bot).
- **Liquidity provision at the close has become cheaper and more competitive**; the NY Fed attributes the death of the
  overnight drift to smaller closing imbalances as algorithmic liquidity providers slice flow
  ([Boyarchenko et al.](https://libertystreeteconomics.newyorkfed.org/2026/07/the-disappearing-overnight-drift/) [V, H]).
- Low-latency statistical arbitrage needs co-location, memberships and an execution team, which a home office lacks
  ([Longmore, Robot Wealth](https://robotwealth.com/the-questions-that-change-everything/) [V, M]).

**For the lab:** market making is a latency and inventory business. The lab should use maker orders to cut its own
costs (recommendation 4), not to earn the spread.

### 1.9 Insider buying after 2008

- **Firm-level clusters:** a test of every Form 4 file from 2006 to Q1 2026 (17,018 usable clusters of two or more
  insiders buying the same week, $100k or more) found a 21-day market-adjusted mean of +1.07% but a median of −0.76%.
  For tradable stocks with three or more insiders, 2006-16 earned +0.89% (t = 2.70) and 2017-2026 −0.42% (t = −1.33);
  2008-09 supplied 94% of the early era's excess return, and without those two years the mean is +0.06% (t = 0.18),
  before costs ([Donttrade, *The insider-buying edge was mostly 2008*](https://donttrade.substack.com/p/the-insider-buying-edge-was-mostly)
  [V, M]).
- **Aggregate timing:** aggregate insider demand forecast market returns in 1978-2004 mostly through growth stocks,
  and showed no reliable power out of sample in July 2004 to September 2009 ([Knewtson, Sias & Whidbee, *FAJ* 2010](https://ideas.repec.org/a/taf/ufajxx/v66y2010i4p46-66.html)
  [V abstract, M]).

**For the lab:** the pre-registration (2026-10-22) expects rejection on SPY and IWM, and its research period starts on
2009-01-02, after the crisis year that carried the firm-level effect. The evidence supports that expectation. Finish
the run when the SEC user agent is set, record it, and stop investing time here.

### 1.10 What independent evaluations say about LLM and AI trading

- **Live fleets show no directional edge.** 3,505 user-funded vaults of a Qwen3-235B agent trading Base memecoins for
  21 days, and 500-599 agents trading Hyperliquid perpetuals from June to August 2026: neither fleet shows a
  directional edge; the perpetuals fleet was unprofitable and trailed a matched retail benchmark (41% vs 50% win
  rate); frontier models were statistically indistinguishable on 416 replayed scenarios
  ([Barton et al., arXiv 2609.05663, Sep 2026](https://arxiv.org/abs/2609.05663) [V abstract, M]: run by the platform
  itself).
- **Tournaments are noise.** In Alpha Arena season 1 (18 Oct - 3 Nov 2025, $10,000 each on Hyperliquid perpetuals),
  Qwen3 Max ended at $12,231, DeepSeek at $10,489, and Claude Sonnet 4.5, Gemini 2.5 Pro, Grok and GPT-5 lost 42-59%
  ([Forklog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/) [S, M]). Seventeen
  days and one account per model can't separate skill from leverage and luck.
- **Long backtests erase the gains.** Over 20 years and 100+ symbols, LLM strategies' reported advantages "often
  vanish", being too timid in bull markets and too aggressive in bear markets
  ([Li, Kim, Cucuringu & Ma, FINSABER, arXiv 2505.07078, KDD 2026](https://arxiv.org/abs/2505.07078) [S, M]).
- **Honest search rejects every LLM-discovered strategy.** With leakage-safe tools and deflation by the agent's own
  trial count, every discovered strategy was rejected while passive benchmarks were certified; a leaky oracle with
  Sharpe 35 passed both the Deflated Sharpe and PBO tests ([Gençay, arXiv 2608.27734, Aug 2026](https://arxiv.org/abs/2608.27734)
  [V abstract, H]). The lab's design already follows this paper (decision 0011).
- **Text inputs can be attacked.** Homoglyph substitutions and hidden text in one day's headlines reduced an
  LLM-plus-LSTM system's annual return by up to 17.7 percentage points over 14 months of real data
  ([Rizvani, Apruzzese & Laskov, arXiv 2601.13082, IEEE SaTML](https://arxiv.org/abs/2601.13082) [V abstract, M]).

**For the lab:** keep AI where it is today: reading, coding, proposing and reporting, with the gauntlet deciding.

---

## 2. Sentiment, attention and social media as signals

### 2.1 What each famous study found

| Study | Signal | Predicts returns? | Predicts volume / volatility? | Held up later? |
|---|---|---|---|---|
| [Tetlock 2007, *JF*](https://ideas.repec.org/a/bla/jfinan/v62y2007i3p1139-1168.html) [V abstract, H] | Pessimism in a WSJ column | Short-term price pressure, then reversal to fundamentals | Extreme pessimism or optimism → high volume | Pattern consistent with noise trading, not information |
| [Loughran & McDonald 2011, *JF*](https://papers.ssrn.com/abstract=1331573) [S, H] | 10-K tone | Tool paper: ~3/4 of the Harvard dictionary's "negative" words aren't negative in finance | n/a | The finance word lists became the standard |
| [Antweiler & Frank 2004, *JF*](https://wernerantweiler.ca/public/noise-a.pdf) [S, M] | Message-board posts | Small | Message counts predict volume and volatility | Classic result: talk predicts activity |
| [Da, Engelberg & Gao 2011, *JF*](https://ideas.repec.org/a/bla/jfinan/v66y2011i5p1461-1499.html) [S, M] | Google search volume for tickers | Higher prices over ~2 weeks, reversed within a year (the abstract could not be opened; this is the paper's widely cited finding) | Attention measure | Attention → temporary price pressure |
| [Preis, Moat & Stanley 2013, *Sci Rep*](https://pmc.ncbi.nlm.nih.gov/articles/PMC3635219/) [V, M] | Google Trends "debt" | +326% Jan 2004 - Feb 2011 vs +16% DJIA buy-and-hold, no costs | n/a | Keyword choice is the biggest bias; Google Trends is no better than past returns, ~17 bp/week ([Challet & Bel Hadj Ayed](https://arxiv.org/abs/1307.4643) [S, M]) |
| [Moat et al. 2013, *Sci Rep*](https://pmc.ncbi.nlm.nih.gov/articles/PMC3647164/) [V, M] | Wikipedia page views | Views of 285 economics pages: 1.10 SD above random (p < 0.001), Dec 2007 - Apr 2012; edits and actor pages: nothing; strongest in 2008 | n/a | Short sample dominated by the crisis; no costs |
| [Bollen, Mao & Zeng 2011](https://arxiv.org/abs/1010.3003) [V abstract, M] | Twitter "calm" mood | 87.6% accuracy on DJIA direction | n/a | **Failed replication**: effect only in the original subsample, no out-of-sample value; the fund built on it closed in 2012 ([Lachanski & Pav, *Econ Journal Watch* 2017](https://econjwatch.org/articles/shy-of-the-character-limit-twitter-mood-predicts-the-stock-market-revisited) [S, H]) |
| [Renault 2017, *JBF*](https://ideas.repec.org/a/eee/jbfina/v84y2017icp25-40.html) [V abstract, M] | StockTwits, half-hourly | First half-hour sentiment change predicts SPY's last half-hour, driven by novices | n/a | Intraday noise-trader effect |
| [Divernois & Filipović 2024, *Digital Finance*](https://ideas.repec.org/a/spr/digfin/v6y2024i2d10.1007_s42521-023-00102-z.html) [V abstract, M] | StockTwits polarity | Same-day yes; next-day no on average; yes when message volume spikes | Volume spikes matter | Conditional effect only |
| [Barber, Huang, Odean & Schwarz, Robinhood herding](https://papers.ssrn.com/abstract=3715077) [S, M] | Retail herding | Negative: about −4.7% over 20 days after herding, −19.6% for extreme events | Herding predicted by attention | Contrarian signal for attention spikes |
| [Aligrithm on WallStreetBets](https://aligrithm.com/is-attention-a-factor-wsb-herding-bert-sentiment-and-the-meme-confound/) [V, M] | 2.2M WSB posts 2016-2022 | Gross four-factor alpha 112.6%/yr, 64.2% without GME and AMC (t = 2.79); event drift flat; no costs | n/a | Concentrated in meme names, not a factor |
| [Freire, Moin, Quaini & Soebhag, GDELT](https://www.eur.nl/en/news/local-global-how-global-news-predicts-stock-market-movements) [S, L] | 520M news articles, 14 countries | Machine-learning market timing with "significant economic gains"; global news adds to local | n/a | Working paper, not read in full |

### 2.2 LLMs on news: real signal, contaminated backtests

- **GPT-4 headline scores** capture the immediate (non-tradable) reaction with about a 90% hit rate and predict the
  later drift, most for small stocks and negative news; strategy returns **fall as LLM adoption rises**
  ([Lopez-Lira & Tang, arXiv 2304.07619 v6, Oct 2025](https://arxiv.org/abs/2304.07619) [V abstract, H]).
- **Look-ahead and distraction.** Anonymised headlines did better than named ones in sample, so the model's knowledge
  of companies distracts it; the anonymisation advantage loses significance out of sample, which suggests most of the
  in-sample gap was look-ahead ([Glasserman & Lin, arXiv 2309.17322](https://arxiv.org/abs/2309.17322) [S, M]).
- **Point-in-time evaluation is now the standard.** A 2026 framework linking Refinitiv News Analytics to CRSP keeps
  only news released after each model's disclosed data-freshness period, and finds decoder LLMs beat encoders and
  dictionaries on net portfolio performance (no numbers in the abstract) ([Kirtac, arXiv 2609.23703, Sep 2026](https://arxiv.org/abs/2609.23703)
  [V abstract, M]). For pretrained time-series models, vintages trained on data after the forecast date actually
  forecast worse (higher errors in 18 of 20 US cases; certainty-equivalent returns 1.77 points lower), so leakage
  there is a rule violation but not automatically an inflated result ([Chen, Chen, Chen, Huang & Zhang, arXiv 2609.20554, Sep 2026](https://arxiv.org/abs/2609.20554)
  [V abstract, M]).

### 2.3 What this means

1. **Returns:** sentiment return effects exist, are short (hours to two weeks), sit in small and hard-to-short stocks
   and in negative news, and decay after publication and adoption. Net of costs, for a long-only ETF and crypto lab,
   expect close to nothing.
2. **Volume and volatility:** this is the robust finding, from Tetlock (2007) and Antweiler & Frank (2004) to the
   StockTwits volume-spike result. A sentiment or attention spike is better used to **size** (expect more volatility,
   hold less) than to pick direction.
3. **Attention spikes are contrarian for retail-crowded names** (Robinhood herding).
4. **Any LLM text test must use model vintages trained before the test period, anonymise entities, and screen for
   manipulated text.**

---

## 3. Forecasting events and surviving surprises

### 3.1 What good forecasters do

- In the IARPA tournament behind *Superforecasting* (Tetlock & Gardner, 2015), three things improved both
  calibration and resolution: training in probabilistic reasoning and reference classes, teams that share rationales,
  and tracking the top 2% into elite teams ([Mellers et al., *Psychological Science* 2014](https://escholarship.org/uc/item/4rg4n9vr)
  [S, H]).
- **LLMs versus superforecasters.** On ForecastBench, expert forecasters beat the best LLM (p < 0.001)
  ([Karger, ..., Tetlock, arXiv 2409.19839](https://arxiv.org/abs/2409.19839) [V abstract, H]); in the original
  200-question comparison, superforecasters scored a Brier of 0.096 against 0.122 for the best model
  ([same paper](https://arxiv.org/pdf/2409.19839) [S, M]). Reports in 2026 put top models about 2-3 Brier-index
  points behind superforecasters, with a linear extrapolation of parity around November 2026 that is contested
  ([Good Judgment](https://goodjudgment.com/what-forecastbench-doesnt-measure/) [S, L]).

**For the lab:** the radar should produce base rates and calibrated probabilities with a scored track record (Brier
score per alert type), not narratives.

### 3.2 Prediction-market accuracy

- Market forecasts are "generally quite accurate" and beat moderately sophisticated benchmarks
  ([Wolfers & Zitzewitz, *JEP* 2004](https://www.aeaweb.org/articles?id=10.1257/0895330041371321) [V abstract, H]).
- **Kalshi, over the paper's sample:** the market's median and mode had a perfect record on the day before each FOMC meeting (a
  significant improvement over fed funds futures); 150 days ahead its errors matched the NY Fed's survey of market
  participants; core CPI and unemployment forecasts matched the Bloomberg consensus; **headline CPI forecasts were
  significantly better** than the consensus ([Diercks, Katz & Wright, FEDS 2026-010, Feb 2026](https://www.federalreserve.gov/econres/feds/files/2026010pap.pdf)
  [V, H]). Kalshi's own study claims 40.1% lower errors on year-over-year CPI ([Kalshi research](https://kalshi.com/research/publications/crisis-alpha)
  [S, L]: self-interested).
- **Polymarket calibration** studies by individuals report good calibration improving near resolution, with prices
  compressed toward 50% at long horizons ([LessWrong analysis of 7,661 markets](https://www.lesswrong.com/posts/Hruc6Gwo3vBZFGb6v/empirical-calibration-of-polymarket-analysis-of-7-661)
  [S, L]).

**What markets price today (Polymarket, read through its public API on 2026-10-22)** [V, H for the prices; they are
crowd beliefs, not facts]:

| Question | Price |
|---|---|
| US recession by end of 2026 | 7.5% |
| No Fed cut in 2026 | 96.75% |
| Fed hikes 25 bp in December 2026 | 78.5% |
| Two Fed hikes in 2026 (in total) | 70% |
| Fed cut by the March 2027 meeting | 13% |
| Fed emergency cut before 2027 | 3.3% |

### 3.3 Base rates: are crises predictable?

- **Credit booms:** rapid growth in both credit and asset prices over three years → about a 40% chance of a financial
  crisis within three years, against about 7% in normal times; crises are not "bolts from the sky"
  ([Greenwood, Hanson, Shleifer & Sørensen, *Predictable Financial Crises*, NBER w27396 / *JF* 2022](https://www.nber.org/papers/w27396)
  [V, H]).
- **Bubbles:** an industry that beat the market by 100% over two years had about a 53% chance of a 40% crash within
  two years (20% after a 50% run-up; about 80% after 150%), but no reliably low average future return, because some
  industries never crash and peaks are hard to time ([Greenwood, Shleifer & You, *Bubbles for Fama*, *JFE* 2019](https://www.nber.org/papers/w23191)
  [S, M]).
- **Credit spreads lead the economy:** the excess bond premium, the part of corporate spreads not explained by default
  risk, falls before activity and asset prices do ([Gilchrist & Zakrajšek, *AER* 2012](https://ideas.repec.org/a/aea/aecrev/v102y2012i4p1692-1720.html)
  [V abstract, H]).

### 3.4 Crisis alpha, volatility targeting and tail-hedge cost

- **Trend's crisis record:** profitable in 6 of 8 slow S&P 500 drawdowns and 9 of 10 worst months, 1985-2020; puts in
  7 of 8 and 10 of 10, at a long-run cost ([AQR 2021](https://www.aqr.com/insights/research/white-papers/tail-risk-hedging-contrasting-put-and-trend-strategies)
  [V, H]). Fast crashes favour puts; slow bear markets favour trend.
- **Volatility targeting:** scaling exposure down when volatility is high raised factor Sharpe ratios in sample
  ([Moreira & Muir, NBER w22208 / *JF* 2017](https://www.nber.org/papers/w22208) [V abstract, H]). Across 103 equity
  strategies, though, real-time versions did not systematically beat the unmanaged ones, because the regressions
  behind the alphas are unstable ([Cederburg, O'Doherty, Wang & Yan, *JFE* 2020](https://www.lehigh.edu/~xuy219/research/COWY.pdf)
  [V, H]). Over 60+ assets since 1926, the Sharpe gain appears only for equities and credit, but the reduction in
  extreme losses appears everywhere ([Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & Van Hemert, *JPM* 2018](https://scholars.duke.edu/publication/1370354)
  [S, H]).

**For the lab:** volatility targeting is a risk tool (smaller left tail), not an alpha source. Pre-register it as
such and judge it on drawdown, not on Sharpe.

### 3.5 Early-warning indicators

| Indicator | What it measures | Evidence | Free source |
|---|---|---|---|
| VIX term structure (VIX vs VIX3M), inversion | Price of near-term variance risk | The slope predicts returns of variance swaps, VIX futures and straddles ([Johnson, *JFQA* 2017](https://ideas.repec.org/a/cup/jfinqa/v52y2017i06p2461-2490_00.html) [S, M]) | Cboe daily index history |
| Excess bond premium, high-yield spread | Lenders' risk appetite | [Gilchrist & Zakrajšek 2012](https://ideas.repec.org/a/aea/aecrev/v102y2012i4p1692-1720.html) [V abstract, H] | Federal Reserve Board (EBP), FRED |
| Correlation / absorption ratio | How much of the variance a few factors explain | Spikes preceded major declines ([Kritzman, Li, Page & Rigobon, *JPM* 2011](https://globalmarkets.statestreet.com/research/portal/insights/article/2a8e282a-343b-443c-b362-1634c71d2d26) [S, M]) | The lab's own ETF panel |
| Crypto funding, basis, open interest | Leverage and crowding | High carry predicts crashes ([BIS WP 1087](https://www.bis.org/publ/work1087.htm) [V abstract, H]); funding near 30% before 10 Oct 2025 ([FTI](https://fticonsulting.com/insights/articles/crypto-crash-october-2025-leverage-met-liquidity) [V, H]) | Binance archive |
| Credit and asset-price growth | Boom conditions | [GHSS 2022](https://www.nber.org/papers/w27396) [V, H] | FRED, BIS credit statistics |
| Event probabilities | Crowd forecasts of macro events | [Diercks, Katz & Wright 2026](https://www.federalreserve.gov/econres/feds/files/2026010pap.pdf) [V, H] | Kalshi, Polymarket APIs |

### 3.6 Detection methods and the false-alarm trade-off

- **CUSUM** (Page, 1954; background, not re-verified here) accumulates standardised deviations above a reference value and alarms when the sum crosses a
  threshold h. Raising h lengthens the average run between false alarms (ARL0, which grows roughly exponentially in h
  for well-behaved data) and lengthens the delay before a real shift is caught. The lab's paper book already uses one:
  for the trend-filter near-miss it fires on average about 5.5 years after the edge vanishes (HANDOFF, M7a), which
  shows how slow honest detection of small changes is.
- **Bayesian online change-point detection** keeps a probability distribution over the run length since the last
  change and updates it exactly with each observation ([Adams & MacKay, arXiv 0710.3742](https://arxiv.org/abs/0710.3742)
  [V abstract, H]). Its hazard rate (the prior probability of a change on any day) plays the role of h.
- **The trade-off can't be avoided, only budgeted.** With many detectors, false alarms add up: twenty independent
  detectors each tuned to one false alarm a year give about twenty false alarms a year (my arithmetic). Section 6.3
  turns this into a budget.

---

## 4. AI in October 2026, and what 2027 could look like

### 4.1 Evidence: capability

- **Task length.** METR's estimate of the 50% time horizon (the length of task, in human time, that a model completes
  half the time) for the public frontier in February-March 2026 was about 12 hours (interval 5-61 hours), with
  internal models likely at 16 hours or more; the 80% horizon was about 1.5 hours. Its suite is saturating, and at
  least 16% of successful runs on 8-hour-plus tasks were illegitimate on review ([METR Frontier Risk Report, May 2026](https://metr.org/blog/2026-05-19-frontier-risk-report/)
  [V, H]). The post-2023 doubling time was estimated at 131 days, and 89 days on 2024 onward
  ([METR, Time Horizon 1.1](https://metr.org/blog/2026-01-29-time-horizon-1-1/) [S, M]).
- **Inputs.** Frontier language-model training compute has grown about 5x a year since 2020 (doubling every 5.2
  months), frontier training cost about 3.5x a year, the stock of AI-chip compute 3.4x a year, and OpenAI's annualised
  revenue about 3.2x a year since 2024 ([Epoch AI trends, Feb 2026](https://epoch.ai/data/trends) [V, H]).
- **Speed-up of AI research itself:** companies reported no dramatic acceleration from AI automating AI research;
  Anthropic said it had not seen a 2x increase in the pace of progress as of April 2026 ([METR](https://metr.org/blog/2026-05-19-frontier-risk-report/)
  [V, H]).

### 4.2 Evidence: economy and labour

- **Productivity:** METR's randomised trial found small gains (about 4-20%), while self-reports suggested 1.6-4x
  ([METR](https://metr.org/blog/2026-05-19-frontier-risk-report/) [V, H]). A task-based model bounds the 10-year TFP gain
  at about 0.66% (0.53% adjusted) ([Acemoglu, *Economic Policy* 2025 / NBER w32487](https://www.nber.org/papers/w32487)
  [S, H]). A widely cited MIT NANDA survey said 95% of organisations saw no P&L return from generative-AI pilots, from
  a small, preliminary sample ([Virtualization Review on MIT NANDA, Aug 2025](https://virtualizationreview.com/Articles/2025/08/19/MIT-Report-Finds-Most-AI-Business-Investments-Fail-Reveals-GenAI-Divide.aspx)
  [S, L]).
- **Jobs:** employment of 22-25-year-olds in the most AI-exposed occupations is 19% below where it would be had it
  tracked less-exposed peers (June 2026, ADP data; 15% a year earlier); the gap comes through less hiring, not more
  layoffs; there is no economy-wide displacement, and the authors call the results descriptive
  ([Brynjolfsson, Chandar & Chen, Stanford Digital Economy Lab, Aug 2026](https://digitaleconomy.stanford.edu/news/canariesaug26/)
  [V, H]).
- **The Fed** raised its target range to 3.75-4% on 16 September 2026, 12-0, saying inflation remains elevated while
  activity expands at a solid pace with strong productivity growth and robust capital investment
  ([FOMC statement](https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm) [V, H]).
- **The IMF (October 2026)** projects world growth of 3.1% in 2026 and 3.4% in 2027, US growth of 2.3% in both,
  global inflation of 4.7% then 4.1%, oil averaging about $96 in 2026, assuming Hormuz shipments resume by March and
  AI demand moderates; it lists an AI-related stock correction among the main risks
  ([Fortune on the WEO, 13 Oct 2026](https://fortune.com/2026/10/13/imf-global-growth-forecast-2026-ai-economic-divide/)
  [S, H]).
- **The 2026 shock already happened:** Brent rose from about $72 on 27 February to nearly $120 after the Iran war
  began, with a 51% rise in March, and Treasuries sold off with crude rather than acting as a haven
  ([CNBC timeline, Apr 2026](https://www.cnbc.com/2026/04/21/oil-price-iran-war-middle-east.html),
  [Bloomberg via Yahoo](https://finance.yahoo.com/news/bond-traders-already-had-hands-133000660.html) [S, M]);
  Brent was back at $108 on 10 September ([CNN](https://www.cnn.com/2026/09/10/investing/oil-iran-war-diesel) [S, M]).

### 4.3 Evidence: money and concentration

- **Capex:** the four largest spenders' capex is expected at about $760bn in 2026 against $413bn in 2025, with Microsoft
  at $190bn and Meta at least $130bn after Q2 calls ([Statista, 31 Jul 2026](https://www.statista.com/chart/35046/capital-expenditure-of-meta-alphabet-amazon-and-microsoft/)
  [S, M]); other trackers put the four companies at $720-745bn ([MLQ](https://mlq.ai/news/big-techs-2026-capex-range-reaches-720-billion-to-745-billion/)
  [S, L]) and CreditSights about $750bn for the top five ([CreditSights](https://know.creditsights.com/insights/tech-raising-hyperscaler-capex-2026-estimates/)
  [S, M]). Company Q3 calls in late October will revise these.
- **The revenue question:** Sequoia's "$600B question" (June 2024) asked how revenue would cover the implied
  infrastructure bill ([Sequoia](https://www.sequoiacap.com/article/ais-600b-question/) [S, M]). Reported OpenAI
  annualised revenue was about $40-50bn in August-September 2026 ([Sacra](https://sacra.com/c/openai/) [S, L]); Michael
  Burry argues hyperscalers understate depreciation by about $176bn over 2026-28 by stretching chip lives
  ([TheStreet](https://www.thestreet.com/investing/stocks/michael-burry-sends-stark-warning-on-ai-hyperscalers) [S, L]).
- **Concentration:** the top ten S&P 500 companies were 37.03% of index value in September 2026
  ([MacroMicro](https://en.macromicro.me/series/46222/share-of-sp500-top10-companies-total-market-cap) [S, M]); one Q3
  snapshot puts it at 39.2%, with Nvidia alone at 7.95% ([Seeking Alpha](https://seekingalpha.com/article/4952774-fall-2026-snapshot-of-sp-500s-market-cap)
  [S, L]).

### 4.4 Forecasts and scenarios (speculation, clearly labelled)

- **AI 2027** (Kokotajlo, Alexander, Larsen, Lifland & Dean, April 2025) narrates a superhuman coder in March 2027 and
  superintelligence by December 2027 in its "race" ending, with a "slowdown" alternative; the authors say 2027 was
  their modal year, not their median, and later updates pushed the median out ([ai-2027.com](https://ai-2027.com/)
  [V, H that this is what they wrote]).
- **The authors' latest:** in April 2026 Kokotajlo's median for an "automated coder" moved from late 2029 to mid 2028,
  and Lifland's from early 2032 to mid 2030, because METR's doubling time looked faster (about 4 months) and
  Claude Opus 4.6 impressed ([AI Futures Project, Q1 2026 update](https://blog.aifutures.org/p/q1-2026-timelines-update)
  [V, H]).
- **Situational Awareness** (Aschenbrenner, June 2024) called AGI by 2027 "strikingly plausible", from about half an
  order of magnitude a year each of compute and algorithmic gains, with trillion-dollar clusters
  ([situational-awareness.ai](https://situational-awareness.ai/) [V, H]).
- **Crowd forecast:** Metaculus' community median for its "first general AI system" question is reported at about
  January 2033 ([AIMultiple tracker](https://aimultiple.com/artificial-general-intelligence-singularity-timing) [S, L]:
  I could not open Metaculus).
- **Sceptics:** Taleb called the AI investment frenzy naive and expects today's leaders to be displaced
  ([Bloomberg, Feb 2026](https://www.bloomberg.com/news/videos/2026-02-23/nassim-taleb-warns-on-software-bankruptcies-volatility-video)
  [S, L]); Acemoglu's macro bound and Burry's depreciation argument are above.

**Plausible 2027 regimes.** These are scenarios for stress-testing, not predictions; I give no probabilities beyond
what markets price (section 3.2).

| Regime | Early signs the radar can watch | What has tended to work | What has tended to fail |
|---|---|---|---|
| A. AI boom continues, inflation sticky, Fed hikes | Capex guidance up, CPI surprises up, Fed odds of hikes | Trend (rates, energy), cash yields | Long-duration bonds, rate-sensitive equities |
| B. AI capex disappointment, mega-cap drawdown | Capex cuts, credit spreads of AI borrowers, correlation spike, VIX inversion | Trend after the first leg, volatility targeting, puts in a fast leg | Cap-weighted equity (37-39% in ten names), crypto beta |
| C. Energy or geopolitical escalation, stagflation | Oil, Hormuz news volume, breakevens, stocks and bonds falling together | Trend (2022 and 2026 pattern), commodities | 60/40, long bonds |
| D. Productivity surprise, disinflationary growth | Productivity data, earnings breadth beyond tech | Broad equity, trend | Shorts, volatility buying |
| E. Crypto-specific failure (exchange, stablecoin) | Funding and open-interest extremes, depegs, outflows | Being flat or small in crypto, venue diversification | Cash-and-carry on one venue, unified margin |

The base rates in section 3.3 apply to B: the bubble literature says crashes become likely after very large run-ups,
while average returns can stay positive for a long time before. That is exactly the case where forecasting a date is
useless and robustness pays.

### 4.5 What a systematic lab can and can't do about it

- **Can't:** forecast whether 2027 is A, B, C, D or E; trade AI headlines faster than the market; validate any
  AI-regime rule on the few past episodes (2000, 2008, 2020, 2022 are four data points).
- **Can:** hold sleeves that worked in different regimes (trend and carry in A and C, cash and volatility targeting in
  B); detect regime breaks with a known false-alarm rate; pre-commit de-risking rules; stress-test the paper book
  against each scenario; and survive operational failures (data feed down, exchange frozen, broker API change).

---

## 5. Practitioners and the social pulse

### 5.1 Practitioners

| Who | What they say that bears on this | Source |
|---|---|---|
| **Rob Carver** | Breadth (214 futures instruments); pooling forecast weights by asset class beat no pooling out of sample; exponentially weighted Sharpe estimates add nothing over all data, except a very slow one to drop a dead rule; not a fan of crypto ("the least stupid way" if you insist); fifth book, *The Art and Science of Trading*, due 1 Dec 2026 | [qoppac.blogspot.com](https://qoppac.blogspot.com/) [V, H] |
| **Kris Longmore** (Robot Wealth) | Ask "what games can I play?"; individuals' edges are "noisy and a little mediocre", which is fine; breadth over polish; predictable flows (month-end SPY/TLT) are a real edge he still trades by hand; the portfolio does the heavy lifting | [Robot Wealth](https://robotwealth.com/the-questions-that-change-everything/), [Edge Alchemy](https://edgealchemy.robotwealth.com/p/how-wealth-managers-pay-you-to-trade) [V, H] |
| **Corey Hoffstein** (Return Stacked) | Q1 2026 was good for trend and carry, with energy the driver; 2025 brought whipsaw in some markets | [Get Stacked podcast listings](https://www.audible.co.uk/podcast/Get-Stacked-Investment-Podcast/B0FRHV3S2L) [S, L] |
| **Cliff Asness** (AQR) | Markets have become *less* efficient over 34 years (social media, gamification, low rates); mispricings get bigger and last longer, rewarding those who can hold on | [*The Less-Efficient Market Hypothesis*, SSRN 4942046](https://papers.ssrn.com/abstract=4942046) [S, M] |
| **AQR** (Ilmanen a contributor) | Expected real return of a global 60/40 is 3.4%, risk premia compressed after three strong equity years; puts cost more than trend as a hedge | [AQR 2026 capital market assumptions](https://www.aqr.com/insights/research/alternative-thinking/2026-capital-market-assumptions-for-major-asset-classes) [V, H]; [AQR 2021](https://www.aqr.com/insights/research/white-papers/tail-risk-hedging-contrasting-put-and-trend-strategies) [V, H] |
| **Campbell Harvey** | Calendar rebalancing is predictable and front-run ($16bn a year); volatility targeting cuts left tails everywhere and raises Sharpe only for risk assets | [NBER w33554](https://www.nber.org/papers/w33554) [V, H]; [JPM 2018](https://scholars.duke.edu/publication/1370354) [S, H] |
| **Marcos López de Prado** | The lab already uses his 2026 Sharpe-inference standard; I found no new 2025-26 work in this pass | [decision 0004](../decisions/0004-gauntlet-design.md) |
| **Ernie Chan** | Conditional parameter and portfolio optimisation (adapting to regimes with ML); no 2026 post found | [IBKR Quant](https://www.interactivebrokers.com/campus/ibkr-quant-news/conditional-parameter-optimization-adapting-parameters-to-changing-market-regimes/) [S, L] |
| **Andrew Lo** | LLMs could technically manage money within about five years, but need protective layers first | [Advisor Perspectives, Jul 2025](https://www.advisorperspectives.com/articles/2025/07/22/mits-andrew-lo-sees-ai-ready-run-money-five-years) [S, M] |
| **Philip Tetlock** | Co-author of ForecastBench: experts still beat the best LLM | [arXiv 2409.19839](https://arxiv.org/abs/2409.19839) [V, H] |
| **Nassim Taleb** | Markets underprice structural risk; AI leaders are not durable | [Bloomberg, Feb 2026](https://www.bloomberg.com/news/videos/2026-02-23/nassim-taleb-warns-on-software-bankruptcies-volatility-video) [S, L] |
| **Matt Levine** | Prediction markets are full of insider trading, and the venues are only starting to police it | [Money Stuff, 25 Feb 2026](https://www.bloomberg.com/opinion/newsletters/2026-02-25/kalshi-found-some-insider-traders) [S, M] |
| **Quantocracy** (aggregator) | October 2026 front page: PEAD dated dead in 2016; insider buying mostly 2008; a closed-form IS→OOS haircut; CTREND's overfit tell; turn-of-month and attention tests | [quantocracy.com](https://quantocracy.com/) [V, H] |
| **Wes Gray / Alpha Architect** | A post on AI-assisted replication of empirical finance appeared in October 2026, but the site refused my requests | [alphaarchitect.com](https://alphaarchitect.com/empirical-finance/) (blocked) |

**The IS→OOS haircut is a formula, not 50%.** For one signal with a true Sharpe of 0.5, the expected out-of-sample
fraction kept is about 0.20 after 1 year of training data and 0.72 after 10 years; the 50% rule fits only one cell;
the formula doesn't price discarded trials (that's what the lab's Deflated Sharpe is for)
([Aligrithm on Jacquier, Muhle-Karbe & Mulligan 2025](https://aligrithm.com/the-real-is-oos-sharpe-haircut-closed-form-not-50/)
[V, M]).

**PEAD's death can be dated.** On point-in-time S&P 500 data (26,892 events, 10 bp a side), the earnings-surprise
strategy had a net Sharpe of +1.08 through 2015 and −0.30 from 2016 ([Dead Signals Lab](https://deadsignalslab.substack.com/p/pead-died-in-2016-and-the-death-can)
[V, M]).

### 5.2 Communities

- **Hacker News** (through the Algolia API): the most discussed 2026 item on prediction-market bots was "Nothing Ever
  Happens", a bot that always buys No on non-sports Polymarket markets (471 points, 276 comments); its README says it
  is for entertainment and reports no results ([GitHub](https://github.com/sterlingcrispin/nothing-ever-happens),
  [HN search](https://hn.algolia.com/api/v1/search?query=polymarket%20bot&tags=story) [V, H]). Retail algorithmic
  trading stories in 2025-26 drew little engagement apart from open-source platforms (NautilusTrader, 208 points), and
  the honest post-mortems were small: "We lost $270 building a Polymarket trading bot", "Adverse selection eating away
  my Polymarket bot's arbitrage profits" ([HN search](https://hn.algolia.com/api/v1/search?query=polymarket%20bot&tags=story)
  [V, M]).
- **Substack and independent blogs** carry the best replication work this year (Dead Signals Lab, Aligrithm, Quanter
  Lab, Donttrade, kacho.io): small, careful, mostly negative on published effects, and mostly without costs. Treat them
  as good leads with M confidence.
- **Reddit, X and Bluesky could not be read** (section 8). The community pulse here is therefore Hacker News and
  Substack only.

---

## 6. Critique of quant-lab, and recommendations

### 6.1 What the lab gets right

The lab already does what most retail labs never do, and what the 2026 literature recommends: every trial counted
(Gençay 2026), look-ahead impossible by construction, a fixed lockbox, thresholds from calibration only, pre-registered
real candidates, forward paper evidence before money, and costs in every backtest. None of that should change. The
problems are about **where** it searches and **what unit** it judges, not about honesty.

### 6.2 What is missing

1. **The unit of evidence is one strategy.** Decision 0004's own numbers: a 0.5 edge is caught 52% of the time with 20
   years and 15% with 10; the paper book's band for a 0.33 edge excludes zero only after about 21 years. Realistic
   premia (Sharpe 0.2-0.5) can't pass alone, so a rejection says little about them. Independent edges combine as
   s·√k; the lab has no pre-registered portfolio track.
2. **Every candidate is judged as if it had no prior evidence.** Trend has a century of out-of-sample history across
   asset classes; a mined RSI pattern has none. The false-discovery stage already uses a prior (5% real ideas); a
   pre-registered prior per evidence class (premia with decades of out-of-sample evidence, published anomalies,
   mined patterns) would be more honest, not less, if it is fixed in advance and calibrated.
3. **The universe misses the best-documented premia.** 21 ETFs and spot crypto: no futures, no perpetuals or funding,
   no volatility instruments. Trend works through breadth across futures (Carver, AQR); carry needs futures or perps.
4. **Mining searches the most crowded space.** Price-only formulas on SPY, BTC and the top-30 coins are what every
   retail trader and every published anomaly paper has already tried (Chen & Welch; FactorBench in decision 0011).
   And every mined trial raises N on that instrument, making the next well-motivated hypothesis harder to validate.
5. **Costs assume taker execution only.** No maker model, no fill-rate or adverse-selection model, no funding costs.
   The ETH breakout lead (decision 0015) depends on this.
6. **Decay inside the research period is invisible.** PEAD died in 2016, the overnight drift in 2021, the pre-FOMC drift
   after 2015; a rule judged over 2009-2024 as one block can pass on its first half. The gauntlet has walk-forward and
   regime stages, but no explicit "did this stop working?" diagnostic.
7. **No regime or event layer.** The paper book's CUSUM watches a strategy's own decay. Nothing watches the market's
   state, and there are no scenario stress tests of the book.
8. **Operational fragility.** The server isn't deployed; Yahoo's lag already cost two decisions; Binance is the single
   crypto venue (10 October 2025 showed venue-specific prices and auto-deleveraging).

### 6.3 Recommendations, in detail

**1. A pre-registered portfolio-of-premia track (highest EV).**

- **What:** pre-register 5-8 sleeves with published rules and defaults: multi-asset ETF trend (already in the paper
  book), crypto trend on BTC/ETH, a crypto carry sleeve (after recommendation 2), the low-volatility crypto near-miss,
  a month-end flow sleeve (recommendation 5). Fix equal-risk weights in advance; no fitted weights. Judge the
  combined portfolio once, with its own lockbox, against the owner's actual alternative (for example a global equity
  ETF or 60/40), plus the paper book.
- **Why:** illustrative arithmetic: six sleeves of Sharpe 0.3 with average correlation 0.2 combine to
  0.3·√(6/(1+5·0.2)) ≈ 0.52; at 10% volatility that is about 5% a year above cash before decay, or about $5,000 a year
  on $100,000. Even 0.52 needs about 15 years to reach t ≈ 2, so the track also needs recommendation 8's
  evidence-class prior to say anything in the owner's lifetime.
- **Cost:** $0, 2-3 weeks (the portfolio engine and panel gauntlet exist).
- **Risks:** sleeves correlate in a crash; the temptation to tune weights after seeing results (pre-registration
  prevents it).
- **Evidence:** decision 0004 calibration; [Longmore](https://robotwealth.com/the-questions-that-change-everything/);
  [Carver](https://qoppac.blogspot.com/); [Hurst, Ooi & Pedersen](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing);
  [AQR 2021](https://www.aqr.com/insights/research/white-papers/tail-risk-hedging-contrasting-put-and-trend-strategies).

**2. Crypto perpetuals and funding.**

- **What:** ingest Binance's USD-M funding-rate, premium-index and metrics files (checksummed, free); add a perp cost
  preset (maker 0.02%, taker 0.05% as reported, plus funding paid or received); test (a) a delta-neutral carry sleeve
  that holds only when funding is above its baseline, (b) funding and basis extremes as a crash-risk input to the
  radar, (c) the existing trend rules on perps instead of spot.
- **Why:** carry is the best-documented crypto premium (Sharpe 6.45 in 2020-25) and a different return source from
  trend; high carry predicts crashes (BIS). Compression since 2024 lowers the expected return, so the honest
  expectation is a modest sleeve plus a valuable signal.
- **Cost:** $0, 1-2 weeks.
- **Risks:** venue failure and auto-deleveraging (10 October 2025), collateral depegs, and the owner's legal access to
  derivatives, which must be checked first.
- **Evidence:** [BIS WP 1087](https://www.bis.org/publ/work1087.htm); [Borri et al.](https://arxiv.org/html/2510.14435);
  [He et al.](https://arxiv.org/abs/2212.06888); [FTI](https://fticonsulting.com/insights/articles/crypto-crash-october-2025-leverage-met-liquidity).

**3. The radar as a risk overlay, with stress tests.**

- **What:** the design brief in section 6.4; a pre-registered volatility-targeting and de-risking rule for the paper
  book, judged on drawdown and on the cost of false alarms; scenario stress tests of the book (regimes A-E, section
  4.4) run on demand and in the daily report.
- **Why:** volatility targeting reliably cuts the left tail (Harvey et al.) even though it doesn't reliably add alpha
  (Cederburg et al.); crises have base rates (GHSS) and leverage signals (BIS); 2027 risks are two-sided.
- **Cost:** $0 (FRED, Cboe, Binance, GDELT, Kalshi and Polymarket APIs are free), 2-4 weeks.
- **Risks:** too many false alarms; detectors fitted to past crises. The false-alarm budget and a replay with a
  lockbox address both.

**4. An execution study before any cheaper-cost re-test.**

- **What:** on Alpaca paper and Binance testnet, place small resting limit orders alongside the model's market orders
  and record fill rates, time to fill, and the price move after fills (adverse selection); then pre-register a new
  question for the ETH range breakout with a measured maker-cost preset and its own N.
- **Why:** the ETH breakout's gross edge Sharpe of 1.24 (p = 0.004) breaks even at 12 bps a side; perps or maker fees
  are far below that. But a breakout entry chases price, and resting orders fill when price runs through them; the
  Polymarket bot shows fill rates can collapse.
- **Cost:** $0, 1-2 weeks.
- **Risks:** testnet fills are not real fills; the re-test spends a new lockbox.
- **Evidence:** decision 0015; [kacho.io](https://kacho.io/why-my-polymarket-arbitrage-bot-lost-money);
  [Barbon et al.](https://papers.ssrn.com/abstract=3925725).

**5. A flow and calendar sleeve.** Pre-register the SPY/TLT month-end reversal (day 15 to month end, published rule) and
the shifted turn-of-month window, on ETFs at 2-3 bps a side. EV is small (the documented effect is tens of basis points
a month), but it is cheap, low-turnover and unlike trend. Risk: decay as more traders front-run. $0, 1 week.
Evidence: [Harvey, Mazzoleni & Melone](https://www.nber.org/papers/w33554); [Longmore](https://edgealchemy.robotwealth.com/p/how-wealth-managers-pay-you-to-trade);
[Quanter Lab](https://quanterlab.com/research/in-search-of-the-market-s-payday).

**6. Futures data for real trend breadth.** 50-100 futures markets at about $50-150 a month (SCALING.md) is what
managed-futures funds trade. Worth it once capital can hold that many positions (micro contracts help; below roughly
$100k the breadth is limited, a judgement I did not verify) or for career value. 2-4 weeks including rolls.

**7. Refocus mining.** Keep the mining machinery, but spend the budget on new inputs (funding, open interest,
calendar flows, cross-asset signals) rather than more price-only formulas on SPY and BTC; lower the per-question trial
caps on crowded instruments. Cost: days; it saves compute and protects N for well-motivated hypotheses.

**8. Gauntlet diagnostics (calibrate before changing any threshold).**

- A **decay diagnostic** on every card: the edge in the last third of the research period, and a change-point
  probability on the research-period edge (BOCPD). Reported, not gated, until a calibration study shows its noise rate.
- **Portfolio-level judging** for recommendation 1, and an evidence-class prior for the false-discovery stage, fixed
  before any run.
- The **gross-plateau study** that decision 0015 proposed for high-turnover families.
- Carver's **very slow EWM** of a live rule's Sharpe as a retirement signal, next to the existing CUSUM.
- $0, 1-3 weeks.

**9. Sentiment and LLM research aimed at volatility, not returns.** Use GDELT event volume and tone, Wikipedia page
views and HN story counts to forecast next-week realised volatility of related ETFs and coins (the robust finding),
feeding position sizing. Any return claim must use model vintages trained before the test period, anonymised text and
a filter for homoglyphs and hidden text. $0-20 a month (local model), 2-3 weeks.

**10. Prediction markets as data.** Pull Kalshi and Polymarket prices for Fed, CPI, recession and geopolitical events
into the radar; run one historical calibration study of favourites net of fees, research only. $0, 1 week.

**11. Housekeeping.** Deploy the server (€8 a month), set `QLAB_SEC_USER_AGENT`, run and record the two insider
verdicts, then close Pipeline C unless it surprises.

### 6.4 Design brief for the radar (`qlab.radar`)

**Purpose.** Detect changes in market state fast enough to act, with a measured false-alarm rate, and turn them into
pre-registered actions (size down, pause adding risk, alert the owner). It is a risk layer, not a signal generator.

**Sources (free, point-in-time where possible):**

| Family | Source | Frequency | Notes |
|---|---|---|---|
| Volatility | Cboe VIX, VIX3M, VVIX history and Deribit DVOL (availability not re-checked here); Binance BVOL (checked) | Daily | Term-structure slope and inversion |
| Credit | FRED high-yield and BBB spreads; the Fed Board's excess bond premium (availability not re-checked here) | Daily / monthly | Store the publication date, since the EBP is revised |
| Cross-asset correlation | The lab's own ETF and crypto panels | Daily | Absorption ratio (share of variance in the first fifth of components), average pairwise correlation |
| Crypto leverage | Binance funding, premium index, open interest, liquidation snapshots | 8-hourly / daily | Venue-specific; add a second venue's public data to catch divergences like USDe on 10 Oct 2025 |
| Event probabilities | Kalshi and Polymarket public APIs | Hourly | Fed, CPI, recession, war and AI-related markets; Polymarket's API rate-limits bursts (connection resets seen on 2026-10-22) |
| News and attention | GDELT 2.0 (15-minute updates); Wikipedia pageviews API; HN Algolia; arXiv counts | 15 min - daily | Theme volumes ("Strait of Hormuz", "tariff", "data center", "Nvidia"), not tone alone |
| Slow AI indicators | Capex guidance from 8-K and earnings, Epoch and METR releases, the Stanford "canaries" dashboard | Monthly / quarterly | Context for scenarios, never a trade trigger |

Reddit, X and Bluesky search could not be reached from this machine (section 8); don't build on them.

**Detectors:**

- Per series: robust z-scores of changes against an EWMA baseline; one-sided CUSUM on standardised residuals; BOCPD
  with a hazard of about 1/250 per day for regime breaks (Adams & MacKay).
- Multivariate: absorption ratio and average correlation; a simple count of families in alarm.
- Thresholds set on the series' own calm history by bootstrap, never on the crises they are meant to catch.

**False-alarm budget (proposed targets, to be confirmed by replay):**

| Tier | Rule | Target false alarms | Action |
|---|---|---|---|
| 1, watch | Any single detector; per-detector ARL0 ≥ 250 trading days | Many (shown, not sent) | Dashboard only |
| 2, alert | Two or more detectors from different families within 5 days | ≤ 6 a year in replay | Telegram message with the base rate and what fired |
| 3, act | Pre-registered combination (for example VIX inversion plus credit widening plus correlation spike) | ≤ 1-2 a year in replay, with the cost of each false action measured | Volatility-targeted size-down and a pause on new risk; never increases risk |

**Evaluation:** replay 2007-2025 point-in-time and count detection delay and false alarms against a dated event list of well-known episodes (not re-verified here:
2008, August 2015, February 2018, March 2020, 2022, March 2023, August 2024, April 2025, 10 October 2025, the 2026
oil shock)); keep the lab's lockbox from 2024-10-01 for the final check; score every tier-2 alert's stated probability
with a Brier score in the daily report.

**Hypotheses worth pre-registering (each with direction, horizon, data cut and N):**

| # | Hypothesis | Evidence |
|---|---|---|
| H1 | Top-decile BTC/ETH funding or basis predicts lower 1-4-week returns and a higher chance of a 20% drawdown | [BIS WP 1087](https://www.bis.org/publ/work1087.htm); [FTI](https://fticonsulting.com/insights/articles/crypto-crash-october-2025-leverage-met-liquidity) |
| H2 | Volatility targeting the paper book cuts its maximum drawdown by a stated amount without lowering its edge Sharpe by more than a stated amount | [Harvey et al. 2018](https://scholars.duke.edu/publication/1370354); [Cederburg et al. 2020](https://www.lehigh.edu/~xuy219/research/COWY.pdf) |
| H3 | VIX above VIX3M, as a de-risk trigger for equity sleeves, lowers drawdown more than it costs in missed rebounds | [Johnson 2017](https://ideas.repec.org/a/cup/jfinqa/v52y2017i06p2461-2490_00.html) |
| H4 | The SPY/TLT month-end reversal (published rule) has a positive edge after ETF costs | [Harvey, Mazzoleni & Melone](https://www.nber.org/papers/w33554); [Longmore](https://edgealchemy.robotwealth.com/p/how-wealth-managers-pay-you-to-trade) |
| H5 | GDELT theme volume and Wikipedia views predict next-week realised volatility (not returns) of the related ETF | [Tetlock 2007](https://ideas.repec.org/a/bla/jfinan/v62y2007i3p1139-1168.html); [Antweiler & Frank](https://wernerantweiler.ca/public/noise-a.pdf); [Moat et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC3647164/) |
| H6 | Kalshi and Polymarket macro probabilities improve the radar's regime classification over price data alone | [Diercks, Katz & Wright](https://www.federalreserve.gov/econres/feds/files/2026010pap.pdf) |
| H7 | Prediction-market favourites (≥ 90 cents) earn a positive return after each venue's fees (research only, no trading) | [Cardozo & Rivero-Wildemauwe](https://arxiv.org/abs/2609.12878); [Qiu & Qiu](https://arxiv.org/abs/2610.13593) |

---

## 7. Corrections to the first pass

- **BIS WP 1087:** the page I read is dated 4 April 2023 and says carry averaged above 10% a year and reached up to
  60%; I could not confirm an October 2025 revision or the ">40%" figure.
- **Crypto carry Sharpe 6.45 / 4.06 / negative:** confirmed, from Borri, Liu, Tsyvinski & Wu (arXiv 2510.14435), on
  Binance data from August 2020 to May 2025.
- **Polymarket favourites "+0.3% to +0.8% gross":** the paper reports +0.83 cents per dollar for purchases at 90 cents
  or more; I found no 0.3% figure.
- **Reichenbach & Walther (SSRN 5910522):** SSRN refused the page and a search found no such paper; I could not verify
  it, so it is not used.
- **Overnight drift fading:** now verified from the NY Fed's own post (July 2026).
- **Donttrade's insider post** is about firm-level clusters, not the aggregate timing rule the lab pre-registered. Both
  point the same way.
- **2025 trend weakness** is confirmed, but 2026 reversed it: trend is one of the best-performing strategies of 2026.

## 8. What could not be read

- **Reddit:** refused by the built-in browser ("not allowed due to safety restrictions") and HTTP 403 to direct
  requests.
- **X:** HTTP 402 to direct requests; no public pages readable.
- **Bluesky:** the public `searchPosts` API returned HTTP 403 on 2026-10-22, from both tools.
- **SSRN:** a bot-verification page that I did not try to get past; SSRN abstracts (Da, Engelberg & Gao; Loughran &
  McDonald; Reichenbach & Walther; Asness) come from other pages or summaries and are tagged [S].
- **CNBC:** "Access Denied" in the browser and 403 to direct requests.
- **Metaculus:** its API requires an account; figures come from a tracker.
- **Alpha Architect, Shleifer's Harvard page:** 403.
- **Binance's fee page** rendered no table; the futures fees are from a secondary source.
- **Polymarket's API** reset connections after a few requests, so only the Fed and recession markets were pulled.

## 9. Glossary

- **Absorption ratio:** the share of the variance of many assets explained by a few principal components; it rises when
  markets move together.
- **ADL (auto-deleveraging):** an exchange closes profitable positions, even hedged ones, to cover accounts it couldn't
  liquidate in time.
- **Adverse selection:** your resting orders fill mainly when better-informed or faster traders know the price is about
  to move against you.
- **ARL0 (average run length):** the average time between false alarms of a detector when nothing has changed.
- **Base rate:** how often something happens in a reference class (for example, crises after credit booms), before
  looking at the specific case.
- **Basis / carry:** the gap between a futures price and the spot price; holding spot and shorting the future earns it.
- **BOCPD:** Bayesian online change-point detection; tracks the probability that the regime changed at each past day.
- **Brier score:** the mean squared error of probability forecasts; 0 is perfect, 0.25 is always saying 50%.
- **Calibration:** of all the times you said 70%, about 70% should have happened.
- **Capex:** capital expenditure, here spending on data centres and chips.
- **Convenience yield:** the extra value of holding something now; in crypto, what leveraged buyers pay through futures
  premiums.
- **Crisis alpha:** gains a strategy tends to make during market crises (trend's reputation).
- **CTREND:** a crypto trend factor combining 28 technical indicators by machine learning (Fieberg et al., JFQA 2025).
- **CUSUM:** a running sum of deviations that raises an alarm when it crosses a threshold.
- **Delta hedging:** offsetting an option's sensitivity to the underlying price by trading the underlying.
- **Distraction effect:** an LLM's knowledge of a company's name biasing how it reads a headline.
- **Elastic net:** a regression that shrinks and drops weak predictors.
- **Excess bond premium (EBP):** the part of corporate bond spreads not explained by expected default; a gauge of lenders'
  risk appetite.
- **Favourite-longshot bias:** cheap, unlikely outcomes are overpriced; likely outcomes slightly underpriced.
- **Funding rate:** the periodic payment between longs and shorts of a perpetual future that keeps it near spot.
- **Gamma imbalance:** dealers' net option exposure; when negative, their hedging pushes prices further in the same
  direction.
- **Hazard rate:** the prior probability, per period, that a regime ends.
- **IS→OOS haircut:** how much of an in-sample Sharpe to expect out of sample.
- **Look-ahead bias:** using information that wasn't available at decision time; in LLMs, a model trained on text from
  after the test date.
- **Maker / taker:** a maker posts a resting limit order (cheaper fees); a taker trades against one (higher fees).
- **OOM (order of magnitude):** a factor of ten.
- **Overnight drift:** the historical tendency of equity futures to rise in the early hours of the European open.
- **PEAD (post-earnings-announcement drift):** prices continuing in the direction of an earnings surprise.
- **Perpetual future (perp):** a future with no expiry, held near spot by the funding rate.
- **Point-in-time model vintage:** a model whose training data ends before the period it is tested on.
- **Pre-FOMC drift:** the historical tendency of stocks to rise in the 24 hours before Fed decisions.
- **Risk premium:** a return paid for bearing a risk others want to avoid, as opposed to a mispricing.
- **Straddle:** a call and a put at the same strike; a bet on movement.
- **Superforecaster:** one of the top forecasters in Tetlock's tournaments.
- **Tail hedge:** a position that pays in crashes, such as out-of-the-money puts.
- **Time horizon (METR):** the human-time length of tasks an AI model completes with a given success rate.
- **Turn of the month:** the historical strength of stocks around month boundaries.
- **Variance risk premium (VRP):** the gap between implied and later realised variance; what option sellers earned.
- **VIX term structure:** VIX at different horizons; "inverted" when near-term implied volatility is above longer-term.
- **Volatility targeting:** scaling a position so its expected volatility stays constant.

## 10. Sources

**[V]** = primary source read; **[S]** = secondary or not opened. 101 sources.

*Anomalies and replication*

1. [V] Chen, A. & Welch, I. (2026). What Useful Alphas? arXiv 2607.06502. https://arxiv.org/abs/2607.06502
2. [S] McLean, R. D. & Pontiff, J. (2016). Does Academic Research Destroy Stock Return Predictability? *JF* 71(1). https://ideas.repec.org/a/bla/jfinan/v71y2016i1p5-32.html
3. [V] Hou, K., Xue, C. & Zhang, L. (2020). Replicating Anomalies. *RFS* 33(5). https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2019-2133..html
4. [V] Jensen, T., Kelly, B. & Pedersen, L. (2023). Is There a Replication Crisis in Finance? *JF* 78(5). https://ideas.repec.org/a/bla/jfinan/v78y2023i5p2465-2518.html
5. [V] Dead Signals Lab. PEAD Died in 2016, and the Death Can Be Dated. https://deadsignalslab.substack.com/p/pead-died-in-2016-and-the-death-can
6. [V] Aligrithm. The Real IS→OOS Sharpe Haircut (Closed Form, Not "50%"). https://aligrithm.com/the-real-is-oos-sharpe-haircut-closed-form-not-50/
7. [V] Quantocracy front page, October 2026. https://quantocracy.com/

*Trend following*

8. [V] Hurst, B., Ooi, Y. H. & Pedersen, L. (2017). A Century of Evidence on Trend-Following Investing. AQR / *JPM*. https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing
9. [V] Sepp, A. & Lucic, V. (2026). The Science and Practice of Trend-Following Systems. arXiv 2607.19497. https://arxiv.org/abs/2607.19497
10. [V] Top Traders Unplugged. Trend Following Performance Report, December 2025. https://www.toptradersunplugged.com/trend-following-performance-report-december-2025/
11. [V] Top Traders Unplugged. Report, May 2026. https://www.toptradersunplugged.com/trend-following-performance-report-may-2026/
12. [V] Top Traders Unplugged. Report, August 2026. https://www.toptradersunplugged.com/trend-following-performance-report-august-2026/
13. [S] Top Traders Unplugged. Reports, April and August 2025 (via search). https://www.toptradersunplugged.com/trend-following-performance-report-august-2025/
14. [V] BarclayHedge. SG Trend Index page (YTD as of 2026-10-22). https://portal.barclayhedge.com/cgi-bin/indices/displayHfIndex.cgi?indexCat=SG-Prime-Services-Indices&indexName=SG-Trend-Index
15. [S] CNBC (6 Oct 2026), via TokenPost. SG CTA Index +15.7% in nine months. https://www.tokenpost.com/news/investing/26960
16. [V] Carver, R. Investment Idiocy blog (Jun-Sep 2026 posts). https://qoppac.blogspot.com/

*Carry and crypto*

17. [V] Schmeling, M., Schrimpf, A. & Todorov, K. Crypto carry. BIS Working Paper 1087. https://www.bis.org/publ/work1087.htm
18. [V] Borri, N., Liu, Y., Tsyvinski, A. & Wu, X. (2026). Cryptocurrency as an Investable Asset Class: Coming of Age. arXiv 2510.14435 v4. https://arxiv.org/html/2510.14435
19. [V] He, S., Manela, A., Ross, O. & von Wachter, V. (2026). Fundamentals of Perpetual Futures. arXiv 2212.06888 v7. https://arxiv.org/abs/2212.06888
20. [S] BitMEX (2025). The Anchor and the Ceiling: Q3 2025 derivatives report. https://www.bitmex.com/blog/2025q3-derivatives-report
21. [V] FTI Consulting. The Crypto Crash of October 2025: When Leverage Met Liquidity. https://fticonsulting.com/insights/articles/crypto-crash-october-2025-leverage-met-liquidity
22. [S] The Defiant. The Ultimate 10/10 Crash Autopsy. https://thedefiant.io/newsletter/defi-daily/the-ultimate-10-10-crash-autopsy
23. [V] Binance public data archive (fundingRate, metrics, premiumIndexKlines, bookDepth, BVOL), checked 2026-10-22. https://data.binance.vision/
24. [S] TradersUnion. Binance futures fees. https://tradersunion.com/brokers/crypto/view/binance/futures-fees/
25. [V] Fieberg, C., Liedtke, G., Poddig, T., Walker, T. & Zaremba, A. (2025). A Trend Factor for the Cross Section of Cryptocurrency Returns. *JFQA* 60(7). https://www.cambridge.org/core/product/4C1509ACBA33D5DCAF0AC24379148178
26. [V] Aligrithm. A Trend Factor for the Crypto Cross-Section (and Its Overfit Tell). https://aligrithm.com/a-trend-factor-for-the-crypto-cross-section-and-its-overfit-tell/

*Volatility and tail hedging*

27. [V] Dew-Becker, I. & Giglio, S. (2026). The decline of the S&P 500 variance risk premium. https://www.dew-becker.org/documents/option_decline.pdf
28. [V] Ilmanen, A., Thapar, A., Tummala, H. & Villalon, D. (2021). Tail Risk Hedging: Contrasting Put and Trend Strategies. *JSI*. https://www.aqr.com/insights/research/white-papers/tail-risk-hedging-contrasting-put-and-trend-strategies
29. [S] Advisor Perspectives (2020). An AQR warning and a 3,612% return fire up the black swan debate. https://www.advisorperspectives.com/articles/2020/05/06/an-aqr-warning-and-a-3-612-return-fire-up-the-black-swan-debate
30. [S] Hedgeweek (2025). Universa up 100% amid April volatility, says allocator. https://www.hedgeweek.com/black-swan-hedge-fund-universa-up-100-amid-april-volatility-says-allocator/
31. [S] Johnson, T. (2017). Risk Premia and the VIX Term Structure. *JFQA* 52(6). https://ideas.repec.org/a/cup/jfinqa/v52y2017i06p2461-2490_00.html

*Calendar and flow*

32. [V] Boyarchenko, N., Larsen, L. C. & Whelan, P. (2026). The Disappearing Overnight Drift. Liberty Street Economics. https://libertystreeteconomics.newyorkfed.org/2026/07/the-disappearing-overnight-drift/
33. [V] Kurov, A., Wolfe, M. H. & Gilbert, T. (2021). The disappearing pre-FOMC announcement drift. *FRL* 40. https://ideas.repec.org/a/eee/finlet/v40y2021ics1544612320315956.html
34. [V] Greenwood, R. & Sammon, M. (2025). The Disappearing Index Effect. *JF* 80(2). https://ideas.repec.org/a/bla/jfinan/v80y2025i2p657-698.html
35. [V] Quanter Lab. The turn of the month effect tested. https://quanterlab.com/research/in-search-of-the-market-s-payday
36. [V] Harvey, C., Mazzoleni, M. & Melone, A. (2025, rev. 2026). The Unintended Consequences of Rebalancing. NBER w33554. https://www.nber.org/papers/w33554
37. [V] Longmore, K. How Wealth Managers Pay You to Trade. Robot Wealth Edge Alchemy. https://edgealchemy.robotwealth.com/p/how-wealth-managers-pay-you-to-trade
38. [V] Longmore, K. The Questions That Change Everything. Robot Wealth. https://robotwealth.com/the-questions-that-change-everything/
39. [S] Barbon, A., Beckmeyer, H., Buraschi, A. & Moerke, M. Liquidity Provision to Leveraged ETFs and Equity Options Rebalancing Flows. SSRN 3925725. https://papers.ssrn.com/abstract=3925725
40. [V] A Comparative Analysis of Overnight vs. Daytime Static and Momentum Strategies Across Sector ETFs (2026). *Risks* 14(4):84. https://www.mdpi.com/2227-9091/14/4/84

*Prediction markets and market making*

41. [V] Cardozo, M. & Rivero-Wildemauwe, J. I. (2026). The Favorite-Longshot Bias in Prediction Markets: Evidence from Polymarket. arXiv 2609.12878. https://arxiv.org/abs/2609.12878
42. [V] Qiu, S. & Qiu, H. (2026). Time-Varying Favorite-Longshot Bias in Polymarket Weather Markets. arXiv 2610.13593. https://arxiv.org/abs/2610.13593
43. [S] Bürgi, C., Deng, W. & Whelan, K. Kalshi favourite-longshot study. CESifo WP 12122. https://www.ifo.de/DocDL/cesifo1_wp12122.pdf
44. [S] Allium. Kalshi Fees: Why the Cost Peaks Near 50 Cents. https://allium.so/blog/kalshi-fees-why-the-cost-peaks-near-50-cents/
45. [V] Diercks, A., Katz, J. D. & Wright, J. (2026). Kalshi and the Rise of Macro Markets. FEDS 2026-010. https://www.federalreserve.gov/econres/feds/files/2026010pap.pdf
46. [S] Kalshi Research. Beyond Consensus: Prediction Markets and the Forecasting of Inflation Shocks. https://kalshi.com/research/publications/crisis-alpha
47. [V] Wolfers, J. & Zitzewitz, E. (2004). Prediction Markets. *JEP* 18(2). https://www.aeaweb.org/articles?id=10.1257/0895330041371321
48. [S] LessWrong. Empirical calibration of Polymarket: analysis of 7,661 markets. https://www.lesswrong.com/posts/Hruc6Gwo3vBZFGb6v/empirical-calibration-of-polymarket-analysis-of-7-661
49. [V] Polymarket public API (gamma-api), Fed and recession markets, read 2026-10-22. https://polymarket.com/event/us-recession-by-end-of-2026
50. [V] kacho.io. I ran an arbitrage bot on Polymarket. Here are the real numbers. https://kacho.io/polymarket-arbitrage-real-numbers
51. [V] kacho.io. Why my Polymarket arbitrage bot lost money. https://kacho.io/why-my-polymarket-arbitrage-bot-lost-money
52. [V] Crispin, S. Nothing Ever Happens (GitHub README). https://github.com/sterlingcrispin/nothing-ever-happens
53. [V] Hacker News Algolia search, "polymarket bot" and "algorithmic trading" stories, 2025-2026. https://hn.algolia.com/api/v1/search?query=polymarket%20bot&tags=story
54. [S] Levine, M. (25 Feb 2026). Kalshi Found Some Insider Traders. Bloomberg Money Stuff. https://www.bloomberg.com/opinion/newsletters/2026-02-25/kalshi-found-some-insider-traders
55. [S] Boston Globe (24 Mar 2026). Kalshi and Polymarket ban insider trading as senators move. https://www.bostonglobe.com/2026/03/24/business/kalshi-polymarket-bans-insider-trading-senators-prediction-markets

*Insiders*

56. [V] Donttrade. The insider-buying edge was mostly 2008. https://donttrade.substack.com/p/the-insider-buying-edge-was-mostly
57. [V] Knewtson, H., Sias, R. & Whidbee, D. (2010). Style Timing with Insiders. *FAJ* 66(4). https://ideas.repec.org/a/taf/ufajxx/v66y2010i4p46-66.html

*AI and LLM trading*

58. [V] Barton, T. J. et al. (2026). What LLM Trading Agents Actually Do in Production. arXiv 2609.05663. https://arxiv.org/abs/2609.05663
59. [S] Forklog. Four out of six AI models suffer losses in trading tournament (Alpha Arena season 1). https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/
60. [S] Li, W. W., Kim, H., Cucuringu, M. & Ma, T. Can LLM-based Financial Investing Strategies Outperform the Market in Long Run? (FINSABER). arXiv 2505.07078. https://arxiv.org/abs/2505.07078
61. [V] Gençay, E. (2026). What survives honest evaluation? arXiv 2608.27734. https://arxiv.org/abs/2608.27734
62. [V] Rizvani, A., Apruzzese, G. & Laskov, P. (2026). Adversarial News and Lost Profits. arXiv 2601.13082. https://arxiv.org/abs/2601.13082

*Sentiment, attention and LLM news*

63. [V] Tetlock, P. C. (2007). Giving Content to Investor Sentiment. *JF* 62(3). https://ideas.repec.org/a/bla/jfinan/v62y2007i3p1139-1168.html
64. [S] Loughran, T. & McDonald, B. (2011). When Is a Liability Not a Liability? *JF* 66(1). https://papers.ssrn.com/abstract=1331573
65. [S] Antweiler, W. & Frank, M. (2004). Is All That Talk Just Noise? *JF*. https://wernerantweiler.ca/public/noise-a.pdf
66. [S] Da, Z., Engelberg, J. & Gao, P. (2011). In Search of Attention. *JF* 66(5). https://ideas.repec.org/a/bla/jfinan/v66y2011i5p1461-1499.html
67. [V] Preis, T., Moat, H. S. & Stanley, H. E. (2013). Quantifying Trading Behavior in Financial Markets Using Google Trends. *Sci Rep* 3:1684. https://pmc.ncbi.nlm.nih.gov/articles/PMC3635219/
68. [S] Challet, D. & Bel Hadj Ayed, A. Predicting financial markets with Google Trends and not so random keywords. arXiv 1307.4643. https://arxiv.org/abs/1307.4643
69. [V] Moat, H. S. et al. (2013). Quantifying Wikipedia Usage Patterns Before Stock Market Moves. *Sci Rep* 3:1801. https://pmc.ncbi.nlm.nih.gov/articles/PMC3647164/
70. [V] Bollen, J., Mao, H. & Zeng, X. (2011). Twitter mood predicts the stock market. arXiv 1010.3003. https://arxiv.org/abs/1010.3003
71. [S] Lachanski, M. & Pav, S. (2017). Shy of the Character Limit. *Econ Journal Watch* 14(3). https://econjwatch.org/articles/shy-of-the-character-limit-twitter-mood-predicts-the-stock-market-revisited
72. [V] Renault, T. (2017). Intraday online investor sentiment and return patterns in the U.S. stock market. *JBF* 84. https://ideas.repec.org/a/eee/jbfina/v84y2017icp25-40.html
73. [V] Divernois, M.-A. & Filipović, D. (2024). StockTwits classified sentiment and stock returns. *Digital Finance* 6(2). https://ideas.repec.org/a/spr/digfin/v6y2024i2d10.1007_s42521-023-00102-z.html
74. [S] Barber, B., Huang, X., Odean, T. & Schwarz, C. Attention-Induced Trading and Returns: Evidence from Robinhood Users. SSRN 3715077. https://papers.ssrn.com/abstract=3715077
75. [V] Aligrithm. Is Attention a Factor? WSB Herding, BERT Sentiment, and the Meme Confound. https://aligrithm.com/is-attention-a-factor-wsb-herding-bert-sentiment-and-the-meme-confound/
76. [S] Erasmus School of Economics on Freire, Moin, Quaini & Soebhag, Global News Networks and Return Predictability. https://www.eur.nl/en/news/local-global-how-global-news-predicts-stock-market-movements
77. [V] Lopez-Lira, A. & Tang, Y. Can ChatGPT Forecast Stock Price Movements? arXiv 2304.07619 v6. https://arxiv.org/abs/2304.07619
78. [S] Glasserman, P. & Lin, C. Assessing Look-Ahead Bias in Stock Return Predictions Generated By GPT Sentiment Analysis. arXiv 2309.17322. https://arxiv.org/abs/2309.17322
79. [V] Kirtac, K. (2026). Financial Language Models ... under Market Frictions. arXiv 2609.23703. https://arxiv.org/abs/2609.23703
80. [V] Chen, H. et al. (2026). Does Training on Future Data Pay? arXiv 2609.20554. https://arxiv.org/abs/2609.20554

*Forecasting, crises and risk management*

81. [S] Mellers, B. et al. (2014). Psychological Strategies for Winning a Geopolitical Forecasting Tournament. *Psychological Science*. https://escholarship.org/uc/item/4rg4n9vr
82. [V] Karger, E. et al. (incl. Tetlock) ForecastBench. arXiv 2409.19839. https://arxiv.org/abs/2409.19839
83. [S] Good Judgment. What ForecastBench Doesn't Measure (Yet). https://goodjudgment.com/what-forecastbench-doesnt-measure/
84. [V] Greenwood, R., Hanson, S., Shleifer, A. & Sørensen, J. A. Predictable Financial Crises. NBER w27396 / *JF* 2022. https://www.nber.org/papers/w27396
85. [S] Greenwood, R., Shleifer, A. & You, Y. (2019). Bubbles for Fama. *JFE* 131(1). https://www.nber.org/papers/w23191
86. [V] Gilchrist, S. & Zakrajšek, E. (2012). Credit Spreads and Business Cycle Fluctuations. *AER* 102(4). https://ideas.repec.org/a/aea/aecrev/v102y2012i4p1692-1720.html
87. [S] Kritzman, M., Li, Y., Page, S. & Rigobon, R. (2011). Principal Components as a Measure of Systemic Risk. *JPM*. https://globalmarkets.statestreet.com/research/portal/insights/article/2a8e282a-343b-443c-b362-1634c71d2d26
88. [V] Moreira, A. & Muir, T. (2017). Volatility-Managed Portfolios. NBER w22208 / *JF* 72(4). https://www.nber.org/papers/w22208
89. [V] Cederburg, S., O'Doherty, M., Wang, F. & Yan, X. (2020). On the performance of volatility-managed portfolios. *JFE* 138. https://www.lehigh.edu/~xuy219/research/COWY.pdf
90. [S] Harvey, C. et al. (2018). The Impact of Volatility Targeting. *JPM* 45(1). https://scholars.duke.edu/publication/1370354
91. [V] Adams, R. P. & MacKay, D. J. C. (2007). Bayesian Online Changepoint Detection. arXiv 0710.3742. https://arxiv.org/abs/0710.3742

*AI, macro and markets in 2026*

92. [V] Kokotajlo, D., Alexander, S., Larsen, T., Lifland, E. & Dean, R. (2025). AI 2027. https://ai-2027.com/
93. [V] AI Futures Project (2 Apr 2026). Q1 2026 Timelines Update. https://blog.aifutures.org/p/q1-2026-timelines-update
94. [V] Aschenbrenner, L. (2024). Situational Awareness. https://situational-awareness.ai/
95. [V] METR (19 May 2026). Frontier Risk Report (February to March 2026). https://metr.org/blog/2026-05-19-frontier-risk-report/
96. [S] METR (29 Jan 2026). Time Horizon 1.1. https://metr.org/blog/2026-01-29-time-horizon-1-1/
97. [V] Epoch AI. Trends in AI (updated 5 Feb 2026). https://epoch.ai/data/trends
98. [V] Brynjolfsson, E., Chandar, B. & Chen, R. (Aug 2026). Canaries update. Stanford Digital Economy Lab. https://digitaleconomy.stanford.edu/news/canariesaug26/
99. [S] Acemoglu, D. The Simple Macroeconomics of AI. NBER w32487 / *Economic Policy* 2025. https://www.nber.org/papers/w32487
100. [V] Federal Reserve (16 Sep 2026). FOMC statement. https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm
101. [S] Fortune (13 Oct 2026). IMF World Economic Outlook coverage. https://fortune.com/2026/10/13/imf-global-growth-forecast-2026-ai-economic-divide/

*Further secondary sources cited in the text:* AQR 2026 capital market assumptions
(https://www.aqr.com/insights/research/alternative-thinking/2026-capital-market-assumptions-for-major-asset-classes, [V]);
Asness, *The Less-Efficient Market Hypothesis* (https://papers.ssrn.com/abstract=4942046); Statista on big-tech capex
(https://www.statista.com/chart/35046/capital-expenditure-of-meta-alphabet-amazon-and-microsoft/); CreditSights
(https://know.creditsights.com/insights/tech-raising-hyperscaler-capex-2026-estimates/); Sequoia, *AI's $600B Question*
(https://www.sequoiacap.com/article/ais-600b-question/); Sacra on OpenAI (https://sacra.com/c/openai/); TheStreet on
Burry (https://www.thestreet.com/investing/stocks/michael-burry-sends-stark-warning-on-ai-hyperscalers); MacroMicro on
S&P 500 concentration (https://en.macromicro.me/series/46222/share-of-sp500-top10-companies-total-market-cap); Seeking
Alpha Q3 snapshot (https://seekingalpha.com/article/4952774-fall-2026-snapshot-of-sp-500s-market-cap); Virtualization
Review on MIT NANDA (https://virtualizationreview.com/Articles/2025/08/19/MIT-Report-Finds-Most-AI-Business-Investments-Fail-Reveals-GenAI-Divide.aspx);
AIMultiple on Metaculus (https://aimultiple.com/artificial-general-intelligence-singularity-timing); CNBC on the
oil-price timeline (https://www.cnbc.com/2026/04/21/oil-price-iran-war-middle-east.html); Bloomberg via Yahoo on bonds
(https://finance.yahoo.com/news/bond-traders-already-had-hands-133000660.html); CNN on oil in September
(https://www.cnn.com/2026/09/10/investing/oil-iran-war-diesel); Bloomberg on Taleb
(https://www.bloomberg.com/news/videos/2026-02-23/nassim-taleb-warns-on-software-bankruptcies-volatility-video);
Advisor Perspectives on Lo (https://www.advisorperspectives.com/articles/2025/07/22/mits-andrew-lo-sees-ai-ready-run-money-five-years);
IBKR Quant on Chan (https://www.interactivebrokers.com/campus/ibkr-quant-news/conditional-parameter-optimization-adapting-parameters-to-changing-market-regimes/);
Get Stacked podcast (https://www.audible.co.uk/podcast/Get-Stacked-Investment-Podcast/B0FRHV3S2L).

*Internal:* decisions 0004, 0006, 0011, 0013-0015, `docs/HANDOFF.md` §4b and M7a, `docs/SCALING.md`,
`docs/preregistration/2026-10-22-m5b-insider-timing.md`.
