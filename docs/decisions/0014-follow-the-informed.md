# 0014. Follow the informed: aggregate insider buying only, as a market-timing rule

- Date: 2026-10-22
- Status: accepted (the verdict waits for the SEC data; see Consequences)

## Context

The roadmap's optional M5b tests "follow the informed" signals as hypotheses, never by copying with money. It
lists three sources:

- **C1:** corporate insiders (SEC Form 4)
- **C2:** big funds (13F)
- **C3:** on-chain "smart wallets" (Polymarket, Hyperliquid)

The owner asked to finish every milestone, as cheaply as possible.

## Decisions

### 1. Only C1, and only in aggregate

- **Insiders are the best-documented case.** Officers and directors must disclose trades within two business days.
  The SEC publishes every filing free, as quarterly structured files since 2006.
- **Aggregate buying across the whole market** has predicted index returns (Seyhun 1988, 1992; Lakonishok & Lee
  2001; Jiang & Zaman 2010).
- **The aggregate avoids survivorship bias.** Firm-level insider strategies need individual stock prices, and free
  stock data lacks delisted companies. The aggregate times an ETF the lab already has bias-free data for: SPY, IWM.
- **C2 is left out.** 13F holdings arrive up to 45 days after the quarter. The published evidence that copying them
  helps is weak, and it would need individual stock data.
- **C3 is left out.** A wallet leaderboard is the lucky tail of thousands of traders: the mining problem without a
  ledger. Copying adds delay and worse fills. The roadmap's own links include a 10-day smart-wallet copy test that
  found no edge. Forward-only tests of it would cost months of monitoring for a hypothesis with no published support.

### 2. The data

- **Code:** `qlab.informed.insiders`.
- **What counts:** original Form 4 filings (amendments are corrections that can arrive years later) with
  non-derivative open-market purchases (`P`) or sales (`S`). Grants, exercises, gifts and tax withholding are left
  out.
- **The series:** per EDGAR filing date, filings with a purchase and with a sale, distinct companies and dollars,
  stored as `sec/INSIDERS/1d` (kind `signals`).
- **Downloads** are cached; only new quarters are fetched (`qlab informed ingest`). The SEC requires a contact
  address in the user agent (`QLAB_SEC_USER_AGENT`). The lab doesn't invent one, and doesn't send the owner's email
  without the owner setting it.

### 3. The rule

`insider_timing` holds the index while the net purchase ratio is above its own usual level, and holds cash
otherwise. The ratio is (purchases − sales) / (purchases + sales) over the last `window` days. It is standardised
against the previous `lookback` days, because insiders sell far more than they buy for reasons unrelated to
information.

- **Lag:** a filing dated d is used from d + 1, since filings accepted after the close keep that day's date.
- **Start date:** the rule declares `starts = 2009-01-02`, and the gauntlet leaves out earlier bars. Without this,
  1993-2008 would count as years in cash. The `starts` field is new on `Strategy`, and any rule whose inputs begin
  later than prices can use it.

### 4. Pre-registered before the data existed

`docs/preregistration/2026-10-22-m5b-insider-timing.md` was committed before the first download. It fixes:

- two questions: `insider_timing:SPY:1d` and `insider_timing:IWM:1d`
- the defaults and the grid (60 settings)
- the research period (2009-2024)
- the expectation: rejection on both, IWM likelier to show something

## Consequences

- **The verdict waits for the data.** On 2026-10-22 the SEC rate-blocked this machine's address after requests
  without a contact address. Requests with the project's public GitHub no-reply address were refused too. The run
  needs the owner to set `QLAB_SEC_USER_AGENT` (for example `quant-lab Juan Rengifo <address>`). Then:
  1. `qlab informed ingest` (about 80 files, a few minutes)
  2. the two pre-registered gauntlet runs, appended to the pre-registration
- **Forward use** of the rule would need daily Form 4 data from EDGAR's daily index, not the quarterly files.
  That is only worth building if a card validates.
