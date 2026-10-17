# Style guide

Short, precise, concrete. Write comments the way a careful engineer explains code to a colleague: what it
is, what goes in, what comes out, and why when the why isn't obvious. No filler.

## Python docstrings

Every module, class and function has a docstring. Functions use the same signature block:

```python
def sharpe(r, rf=None, ppy=252):
    """Annualised Sharpe ratio of periodic returns.

    Args:
        r: Simple returns per period.
        rf: Risk-free return per period, aligned with ``r``. None means zero.
        ppy: Periods per year: 252 for stock days, 365 for crypto days.

    Returns:
        Mean excess return divided by its standard deviation, times sqrt(ppy).
        NaN when there are fewer than two observations or no volatility.
    """
```

Rules:

- **First line:** what the function returns or does, in one sentence, ending with a period.
- **Optional paragraph:** the reason or the method, only if it adds something the name doesn't.
- **Args:** every parameter, with units and allowed values. Write "None means ..." when None has a meaning.
- **Returns:** the type in words and the units. State the edge cases (NaN, empty).
- **Raises:** only exceptions the caller should expect.
- Sections a function doesn't need are left out (no "Args: None").
- **Module docstring:** one line saying what the module is for, then a short paragraph on design decisions.

## Inline comments

- Only where the code can't say it itself: a non-obvious formula, a data quirk, a deliberate trade-off.
- State facts ("Binance switched spot timestamps to microseconds in 2025"), not narration ("now we loop").
- Cite the source for formulas, e.g. `# Bailey & López de Prado (2014), eq. 2`.

## TypeScript

Every exported component and function gets a JSDoc block with the same shape:

```ts
/**
 * Metric tile with a label, a value and an optional glossary tooltip.
 *
 * @param props.label - Text shown above the value.
 * @param props.value - Already formatted value.
 * @param props.term - Glossary key for the info tooltip.
 */
```

## Commits

[Conventional Commits](https://www.conventionalcommits.org/), one feature per commit, pushed straight to
`main`:

```
feat(api): add backtest endpoints
fix(data): read Binance microsecond timestamps
test(gauntlet): calibrate on pure-noise strategies
docs: describe the lockbox in the glossary
```

Scopes: `core`, `data`, `backtest`, `gauntlet`, `inbox`, `mining`, `api`, `web`, `ci`, `docs`.
A milestone ends with a tag: `m0`, `m1`, ...
