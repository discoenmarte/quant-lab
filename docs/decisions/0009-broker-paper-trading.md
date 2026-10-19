# 0009. Broker paper trading: paper-only by construction, limits outside the strategy, friction only on adding risk

- Date: 2026-10-19
- Status: accepted

## Context

The forward paper book (M7a, decision 0007) records each entry's targets before the outcome exists and scores
them with the researched engines. That proves the decisions were honest, but not that they can be traded: fills,
whole shares, cash, timing and costs are all modelled. M7b sends those decisions to real paper accounts, Alpaca
paper for US ETFs and the Binance spot testnet for crypto, so the go-live gate (M8) can rest on executions instead
of assumptions.

Vidovich & Zerjal (2026) traced one live system's drift from its backtest to six defects:

1. a data feed missing most of the opening volume
2. adjusted prices that live orders never see
3. data that existed only live
4. sizing above broker limits
5. UTC read as New York time
6. settings that never reached the order code

None showed in summary statistics. The design below is shaped by that list and by the roadmap's control model.

## Decisions

### 1. Paper only, by construction

Every request goes through one HTTP client (`broker.base.PaperClient`), which refuses any host except
`paper-api.alpaca.markets` and `testnet.binance.vision`, and anything that isn't https. Each adapter has one
fixed base URL. No setting, flag or environment variable can point it at a live endpoint.

The Alpaca adapter also refuses live keys (Alpaca prefixes them `AK`; paper keys are `PK`), and refuses an
`ALPACA_ENDPOINT` setting that isn't the paper host; it never uses that setting as its address. Going live is the
go-live gate, which doesn't exist yet.

### 2. Secrets stay in the process

Keys come from the environment, `data/lab.env` or the repository's `.env`; both files are git-ignored. Alpaca
accepts the names `ALPACA_KEY` / `ALPACA_SECRET`, `QLAB_ALPACA_*` or Alpaca's own `APCA_*`; Binance uses
`QLAB_BINANCE_TESTNET_KEY` / `_SECRET`. Keys travel only in headers, or as an HMAC-SHA256 signature for Binance.
They are never logged, stored, returned by the API or included in an error message; tests check this.

Tests can't see the developer's `.env`: `conftest.py` limits settings to each test's own `lab.env`, so no test can
reach a real paper account.

### 3. Sleeves: an entry trades only when enrolled, with its own slice of the account

A paper-book entry is enrolled with a typed confirmation (its id) and a reason. It gets a sleeve of
`sleeve_capital` (10,000 by default) on the broker for its asset class. A universe mixing ETFs and crypto can't be
enrolled, because it would need two brokers.

A sleeve's holdings and cash are computed from the lab's own fills. Several entries can share one account, and
each keeps its own record and return.

### 4. Orders: raw prices, whole units, long-only, the researched fill

Each night, after the book decides and scores, the latest decision of every active sleeve is sent once. The
order's id is built from the entry, the decision date and the symbol, and brokers refuse a duplicate id.

- **Sizing** uses the **raw** close of the decision's date: research runs on dividend-adjusted prices, but orders
  trade raw ones (defect 2). Quantities round down to whole shares for Alpaca and to the lot step for Binance,
  and dust below 10 is skipped.
- **Long-only, no leverage:** negative targets become zero, because both paper accounts are spot or cash
  accounts. Weights that add up to more than one are scaled down.
- **Order of trades:** sells go first, and buys are cut back to the sleeve's cash.
- **Fill timing:** the engines fill at the next open.
  - Alpaca orders sent between 19:00 and 09:28 New York time go in as market-on-open (`opg`), the opening auction
    itself.
  - At other times Alpaca orders go as day orders. If the auction refuses an order, it is re-sent once as a day
    order.
  - Binance market orders fill at once.
- **Late orders:** every order records whether it was sent after the open it was meant for. A decision whose
  session has already closed isn't sent at all.

### 5. Limits outside the strategy, checked before every order

The limits are:

- the kill switch and pauses
- the largest single order (12,000)
- orders per day (40)
- no leverage inside a sleeve
- all sleeves together at most 90% of the account's equity

A blocked order is stored with the limit and the numbers, and alerts the owner. A rejection by the broker is
stored with its message and alerts too.

Reducing risk is never blocked: selling what a sleeve holds, on a kill or a flatten, bypasses every limit except
"only what the sleeve holds".

### 6. Asymmetric friction (ROADMAP §6b)

Killing, pausing, flattening and tightening a limit take effect when recorded:

- the kill switch is one request in the API and two clicks in the top bar (the first arms it for four seconds)
- no form, so nothing can slow it down

Adding risk takes more:

- **Resuming** (after a kill or a pause) needs the typed word `RESUME` and a reason.
- **Loosening a limit** needs `LOOSEN` and a reason.
- Both take effect **24 hours later**.

Every control action is a row in an append-only log with the time it was recorded and the time it takes effect.
The state is the latest recorded row among those already in effect. So a kill after a pending resume wins, and so
does a tightening recorded while a loosening waits. The kill is recorded before any broker is called, so it holds
even when a broker can't be reached; then open orders are cancelled and every sleeve is sold.

Per the control model, no AI action may add risk. Enrolling, resuming and loosening are the owner's to do.

### 7. Reconciliation and slippage against the model

Before the lab's first order on an account, it stores the account's holdings as a baseline. After every sync,
each holding must equal its baseline plus the lab's fills. Binance buy fees charged in the coin count against the
coin. A difference is stored and alerted once per occurrence; causes include a manual trade, a testnet reset or a
missed fill.

Fills are append-only, keyed by the broker's execution id. Alpaca reports only an order's cumulative quantity and
average price, so each increase since the stored state becomes one fill at the implied price.

Each filled rebalance is compared with the raw open the engine would have filled at, in basis points against the
trade (positive is worse), next to what the cost model charges for half the spread plus slippage. That measures
the cost model with real fills instead of assuming it.

### 8. The nightly job and the screens

The nightly job (decision 0007 §7) now ends its book steps with a broker step:

1. sync the earlier orders' fills
2. reconcile
3. send the new decisions

It goes through the API when the server is up. Its summary counts sleeves by status, new fills and mismatches,
and an error or a mismatch fails the night.

The top bar shows the trading mode and the kill switch on every page. The Trading Desk shows:

- the paper accounts, each sleeve with Flatten, and the order blotter
- slippage against the model
- the enroll, limit and resume forms

## Consequences

- The lab can trade its decisions on paper accounts with no path to real money. Starting is a deliberate owner
  action, and so is coming back from a kill.
- On 2026-10-19 the Alpaca paper account answered (100,000 USD, all cash, no positions) with the owner's paper
  keys. The two paper-book entries aren't enrolled yet: enrolling adds risk, so it is the owner's call.
- Known gaps:
  - **Shorting.** Long-only only.
  - **One broker per sleeve.** A mixed universe can't trade.
  - **Late crypto decisions.** The public archive publishes after the next candle has opened, so crypto
    decisions are always late (decision 0007); a live price feed would fix it.
  - **No holiday calendar.** A decision for a market holiday waits for the next session's open.
  - **Testnet quirks.** It lists only a few dozen pairs and resets now and then, which shows up as rejections and
    reconciliation mismatches.
  - **Telegram is one-way.** Alerts go out, but there is no `/kill` command yet.
