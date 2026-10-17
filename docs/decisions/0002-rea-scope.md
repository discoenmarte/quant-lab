# 0002. Reverse-engineering (REA) is an audit tool, not an alpha source

- Date: 2026-10-17
- Status: accepted

## Context

REA (github.com/morluto/rea, MIT, npm `rea-agents` 6.4.0 as of 2026-10-13) connects coding agents to
reverse-engineering tools: native binaries, Electron/JavaScript apps, .NET, APKs, WASM, EVM bytecode, saved
network captures, SQLite files. Social posts in October 2026 promote it for "reverse-engineering hedge funds"
and trading platforms.

A search of recent sources found no documented, verified trading result produced with REA. One viral claim
(reverse-engineering TradingView to unlock paid indicators) describes unauthorised access. The REA paper and
the October 2026 posts that promote it also promote a paid data platform, and their only published result is
the 200-day trend rule (6.8% a year, same growth as buy-and-hold).

## Decision

REA is allowed in exactly two lawful, optional roles:

1. **Product audit lane (extends M4).** For a trading bot or indicator the user owns or is licensed to
   examine: extract its real rules with REA, write them as a strategy spec, and send the spec through the
   gauntlet. Use the red-flags checklist below. Most audited products are expected to show no edge.
2. **Token contract screen (extends M5b).** Inspect EVM bytecode of a token before it enters any crypto
   study, and flag owner powers such as minting, blacklisting, pausing, or changeable fees.

REA is never used to probe services we don't own, bypass licensing or paywalls, or extract proprietary code.

## Red flags when auditing a product

Look-ahead or repainting signals, rules that change after the fact, a single lucky parameter value ("lone
peak"), martingale or averaging-down sizing, survivor-only universes, results from one market regime,
"AI" that is a fixed rule, and secrets or keys in the code.

## Consequences

No new dependency in the core lab. If the audit lane is built, REA is installed by the user with
`npx rea-agents setup` and Ghidra (free), not commercial disassemblers.
