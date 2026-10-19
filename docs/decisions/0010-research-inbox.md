# 0010. Research inbox: a local model reads abstracts, specs map onto existing templates, replication is judged by its standard error

- Date: 2026-10-19
- Status: accepted

## Context

Pipeline A (roadmap M4) turns published research into candidates: fetch new papers, triage them cheaply, turn
the promising ones into a structured spec, have the owner review it, replicate the paper's claim, test the rule
after publication, and send it through the gauntlet. Three facts shape the design:

- Most of what arrives isn't a strategy at all (market microstructure models, option pricing, agent studies). A
  quick filter has to throw it away without the owner reading it.
- The model doing the reading sees untrusted text. A paper can ask it to score itself 7/7.
- Published results shrink. McLean & Pontiff (2016) found anomaly returns fall by about half after publication,
  so a replication that matches the paper proves the code, not the edge.

## Decisions

### 1. Sources: arXiv's API, politely, plus frontier queries

The feed reads arXiv's Atom API (`export.arxiv.org`, https) for the q-fin categories `TR`, `PM`, `ST` and `CP`,
newest first, with a three-second pause between calls as arXiv asks. Each run fetches the newest listing and then
the **frontier queries**: keyword searches such as "time series momentum" or "momentum cryptocurrency" that reach
older papers on rules the lab can test (`QLAB_INBOX_QUERIES` replaces the defaults). Papers are stored once and
refreshed when arXiv has a newer version.

SSRN and journals have no open API and forbid scraping, so they are out of scope. A paper the owner wants from
there can't be added yet (a known gap below).

### 2. A local model by default, paid ones behind a budget

The default model is `qwen3.6:35b` through Ollama on the owner's Mac: temperature 0, a fixed seed, thinking off.
A warm call takes about seven seconds per abstract, so a pass of a few hundred papers takes half an hour.
Ollama's schema option alone didn't keep the model to the expected keys; the system prompt now lists them.

A paid provider (the `claude` CLI) can replace it, but only under `QLAB_LLM_BUDGET_USD`, which defaults to 0, so
paid calls are refused until the owner sets a budget. Every call, local or paid, is logged in `llm_calls` with its
duration, tokens and cost. The Research Inbox shows the month's spend against the budget.

### 3. Paper text is data

The title and abstract go between `<<<PAPER` and `PAPER>>>` markers, and the system prompt says the text between
them is untrusted and may contain instructions to ignore. The answer is validated strictly:

- every key present and of the right type, nothing extra
- the score is recomputed from the seven dots; the model's own total is ignored
- a paper the model calls "not a strategy" scores 0, whatever its dots say

A failed answer is retried once with the keys spelled out. A second failure is recorded against the paper, and
the run moves on.

### 4. The seven-point rubric

Each dot is a yes or no a reader could check in two minutes:

| Dot | Question |
| --- | --- |
| testable | Can it run on the lab's data (US ETFs, Binance spot)? |
| specified | Is the rule precise enough to code? |
| out_of_sample | Does it report out-of-sample or post-publication evidence? |
| costs | Are transaction costs accounted for? |
| rationale | Is there an economic reason it should work? |
| breadth | Ten years or more, or many instruments? |
| search_disclosed | Does it say how many variants were tried? |

The score sorts the inbox. It isn't evidence: a 7/7 paper still has to pass replication and the gauntlet.

### 5. The deep read: excerpts of the full text

Triage reads only the abstract, which rarely states the sample, the rebalance or the Sharpe ratio. A spec reads
more: arXiv renders most papers submitted since late 2023 as HTML, and `inbox.fulltext` parses that page with the
standard library. It keeps the article (formulas as their LaTeX, tables as rows) and drops the site's menus and
the bibliography. Then it cuts the text to the opening plus the passages about performance, samples, costs and
parameters, about 12,000 characters, in document order. The text is cached under `raw/arxiv`, and the spec
records what it was drafted from (`read`). Older papers have no HTML version and keep an abstract-only spec.

Calls to Ollama ask for a 16,384-token context (`QLAB_LLM_CTX`). Ollama's default would cut off the template
catalog plus the excerpts without any error.

### 6. Specs name existing templates; nothing the model writes is run

The roadmap planned "generate code" and a generated-code diff. The inbox maps a paper onto one of the lab's
existing rules instead: a single-instrument rule (`backtest.strategies`) on an instrument the lab has, or a
portfolio rule (`portfolio.strategies`) on one of its universes, with parameters clamped to the template's ranges
and snapped to its step. Those templates are tested and pass the look-ahead checks; model-written code would be
neither, and running it would mean executing text shaped by an untrusted paper.

A paper no template fits becomes `needs_code`. Whether it deserves a new template is the owner's call, and a new
template is written and tested like any other code.

The spec also records what the paper claims (Sharpe ratio, CAGR, sample period, whether costs were included) and
how the template differs from the paper's rule.

### 7. The owner reviews

A spec starts as a draft. The owner approves it, optionally editing the template, target or parameters, or
rejects it; both need a written reason, and both go to the audit log. Only approved specs are replicated or
judged.

### 8. Replication is judged by the standard error of a Sharpe ratio

The lab runs the approved spec over the paper's claimed sample, clipped to the data it has and to the research
period. The lockbox is never touched. The run's Sharpe ratio counts as replicated when it lies within

    max(0.3, 2 · √((1 + SR²/2) / years))

of the claim: two standard errors of an annual Sharpe estimate over the run's length (Lo 2002), and never tighter
than 0.3. The lab trades ETFs and spot crypto where papers often used futures, single stocks or other periods, so
close is all it can ask. A paper with no Sharpe claim gets the run but no verdict.

The **post-publication** run applies the same rule from the paper's publication date to the end of the research
period. A paper published after the lockbox date has no post-publication research data, so its forward evidence
has to come from the paper book.

Both runs load two years of history before their window (`history_from` on both runners), so a 12-month rule is
already positioned on the window's first day. Only the window is measured and recorded. A window also opens no
earlier than two years after the target's data begins, so no measured day is a warm-up day. A claimed sample
end of "2023" or "2023-12" means the period's last day.

Every run is a ledger trial (source `inbox`) on the template's research question. Trying a paper raises N for
every later candidate on that instrument or universe, as any other trial does.

### 9. The gauntlet and the digest

Sending a spec through the gauntlet is a separate owner step; the spec is then judged like any other candidate
and links to its report card.

`qlab inbox run` (or the Run button) does one pass: fetch, triage, draft specs for the few best new papers, then
the digest. The digest covers the past week: papers seen, the best ones with their dots, specs waiting for
review, replications, gauntlet verdicts and model use. It is saved under `reports/inbox`, and its headline goes
out through the alert channels (decision 0008) with a link to the inbox.

## Results of the first real pass (2026-10-19)

The pass read 225 papers: the newest q-fin listing plus the seven frontier queries. It made 230 model calls in
about 14 minutes on the owner's Mac; the nine full-text drafts that followed took about 3 more. Total cost:
0 USD. Seventeen papers scored 4/7 or more. Most were deep-learning or option-hedging papers that no template
fits.

Claude reviewed the drafts as the owner's delegate, the owner having asked for the whole pipeline to run.
Every claimed number below was checked against the paper's own text, and specs claiming the deep model's result
were edited to the paper's classic benchmark, which is what the template implements.

| Paper (arXiv) | Rule the lab ran | Claim (paper's sample) | Lab (window) | Tolerance | Verdict |
| --- | --- | --- | --- | --- | --- |
| Ong & Herremans, 2306.13661 | TSMOM benchmark → `tsmom` on `multi_asset` | 0.59 (2000–2020, futures, 3 bps) | 0.50 (2009-05 → 2020-12) | 0.64 | replicated |
| DeePM, 2601.05975 | Trend (TSMOM) benchmark → same | 0.45 net (2010–2025, futures) | 0.39 (2010-01 → 2024-09) | 0.55 | replicated |
| DeepUnifiedMom, 2406.08742 | TSMOM(12) benchmark → same | 1.01 (2000–2023, 49 futures, 3 bps) | 0.48 (2009-05 → 2023-12) | 0.64 | replicated |
| Devanathan, Tzikas & Boyd, 2609.07946 | 50/30/20 with volatility control → `vol_managed` on `asset_classes_long` (approximate) | 0.83 (2006–2026, 5 bps) | 0.46 (2006-12 → 2024-09) | 0.55 | replicated |
| Massaad et al., 2412.14361, citing Zarattini & Antonacci | Industry trend, 48 industries, up to 200% gross → `trend_filter` on `sectors` | 1.39 (1926–2024) | 0.63 (2001-01 → 2024-09) | 0.58 | not replicated |
| Singha, 2511.08571 | Gold trend with Kelly sizing and ATR exits → `trend_sma` on GLD (trend core only) | 2.88 (2015–2025, walk-forward) | 0.27 (2015-01 → 2024-09) | 1.45 | not replicated |

Post-publication runs exist for only two papers; the rest were published after the lockbox date:

- Ong & Herremans: 0.96 from June 2023 to the lockbox, 1.3 years.
- DeepUnifiedMom: 2.13 over 0.3 years, which says nothing.

## Consequences

- **The roadmap's exit criterion is met, narrowly.** Three papers' published results replicated within
  tolerance on the lab's own data: the TSMOM benchmarks of 2306.13661, 2601.05975 and 2406.08742. Four
  caveats:
  - **One rule.** All three are the same rule, so this checks the machinery on one template, not on three
    strategies.
  - **A wide tolerance.** At 12 to 15 years, two standard errors are about 0.6. "Within tolerance" means the lab
    can't tell its result from the claim, not that they match. DeepUnifiedMom's 1.01 passes with the lab at
    0.48, less than half.
  - **ETFs, not futures.** The lab's ETF version earns less than the papers' futures versions in every case, as
    expected from fewer markets, weekly rebalancing and a shorter sample.
  - **No classic papers.** The classics behind these rules (Moskowitz, Ooi & Pedersen 2012; Faber 2007) aren't
    on arXiv. Their claims weren't typed in from memory.
- **The two misses fail the right way.** Both claims came from much richer rules: 48 industries with leverage
  since 1926, and a fitted, Kelly-sized gold strategy at 0.9% volatility. The lab tests only their core on its
  own data; a miss rejects that core, not the paper.
- **The method changed after the first runs, and the record shows it.** The first round started each window cold:
  a 12-month rule sat flat through its first year, and one post-publication window never traded at all. The
  second round loaded history before each window. The third opened windows only after two full years of data.
  Every round is in the ledger: 24 trials with source `inbox`, which raise N for these four research questions.
  The verdicts came out the same in all three rounds.
- **Known gaps:**
  - **Sources.** SSRN and journal papers can't be added yet; a manual paper form would fix that.
  - **Older papers.** Papers without an HTML version (before late 2023) get an abstract-only spec; their LaTeX
    source on arXiv would be the next reader.
  - **Bare post-publication runs.** Their windows are often too short to judge, and nothing marks that yet.
  - **No schedule yet.** The weekly digest runs from `qlab inbox run`; scheduling it is the owner's call.
  - **Templates are the ceiling.** The two papers marked `needs_code` (X-Trend, NoLBERT topics) wait for a
    template the owner may or may not want written.
