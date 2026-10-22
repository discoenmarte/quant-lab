# 0013. The go-live gate: a checklist computed from evidence, and no live order path until it is green

- Date: 2026-10-22
- Status: accepted

## Context

The roadmap's M8 is "a decision, not code": a checklist (ROADMAP §7) that must be all true before any real money,
and a screen where it "turns green only when the evidence exists". In October 2026, after M3-M7b and M4-M5:

- No card is validated: 0 of 18 have passed the gauntlet.
- Two rejected near-misses are in the forward paper book.
- One of them trades a paper sleeve with three forward days.

The owner asked to finish every milestone and to make real money.

## Decisions

### 1. Every item of the checklist is computed, not ticked

`qlab.golive.checklist(entry)` reads what the lab already records:

| Group | Item | Evidence |
|---|---|---|
| Research | Passed every gauntlet stage, the lockbox included | card verdict `validated` |
| Research | Deflated Sharpe passed with the ledger's full N | the card's significance stage, with its N |
| Research | Thresholds fixed before the card was judged | the thresholds version's date is before the card's |
| Research | Lockbox passed on its first and only use | the lockbox stage, and one lockbox use on the question, by this card |
| Research | Adds diversification | the diversification stage |
| Paper | At least 63 forward sessions (about three months) | the paper book's scored days; the earliest date is shown |
| Paper | Inside the band, no decay alarm | the latest forward report |
| Broker | At least 20 broker paper fills | filled orders of the entry's sleeve |
| Broker | Slippage within the cost model + 5 bps | mean measured slippage against the model's |
| Broker | Kill switch tested since the sleeve started | a kill in the control log after enrolment |
| Broker | Limits enforced outside any model | the limits in force |
| Plan | Maximum loss written down | the owner's plan |
| Plan | Stop rule written | the owner's plan (a default is suggested) |
| Plan | Money you can afford to lose entirely | the owner's plan, affirmed |

The plan is the only part the owner writes. It is signed with the owner's typed name, appended to `golive_plans`
and audited, and changes nothing else. The page, `qlab golive`, the API and the bot's `/golive` all show the same
checklist. For a rejected card the next step says plainly that it can't go live, whatever paper trading shows.

Today both entries meet 2 of 14 items, and the earliest any could meet the paper-time item is 2027-01-13.

### 2. No live order path yet

The lab still can't send a real-money order. The live adapter and the paper-to-live switch from the roadmap's
screen plan (typed name, reason, 24-hour cooling-off, a red banner) are left out on purpose:

- **Nothing can use them** for at least three months. A card must first pass the gauntlet, which none has, and then
  earn its paper time.
- **Code that can move real money is the riskiest code in the repository.** It can't be tested against a real
  account without risking money. Written now, it would sit unused while the broker's API moves on.
- **The decision belongs to the owner.** On 2026-10-22, the automated session building M8 was stopped by its safety
  check when it started writing a live-money client, and building one needs the owner's explicit approval.

When an entry's checklist is all green, the gate says so. The live path is then built against that entry, and
reviewed by the owner before it can run. It should follow the roadmap's control model:

- live keys separate from paper keys, and a fixed live host
- orders only for an entry whose gate is green and whose cooling-off has passed
- a live capital cap and the owner's loss budget enforced outside the strategy
- automatic flattening on the stop rule
- the kill switch covering live and paper alike

`CLAUDE.md`'s rule stands unchanged: no code path places real-money orders.

### 3. Thresholds of the gate itself

These numbers come from the roadmap and from what the book can measure:

- **63 sessions** is the low end of the roadmap's 3-6 months. Decision 0007 measured that the book catches breaks
  and large decay, not small ones, so this is a floor and not proof.
- **20 fills** is the fewest for a mean slippage that isn't dominated by one order.
- **5 bps tolerance:** paper fills are simulated, so slippage measured on paper is a weak test. The real one is the
  first live month, which the stop rule covers.

## Consequences

- The gate shows the owner why there is no real money yet, item by item, and when the earliest item can turn green.
- Going live will be a separate, reviewed change, with this record as its specification.
