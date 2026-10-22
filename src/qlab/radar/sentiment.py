"""Headline sentiment from word lists: counts of negative and positive words, with simple negation.

Why word lists, and which ones (decision 0016):

- **Method.** Counting tone words is the oldest well-documented way to read financial text: the share of negative
  words in a Wall Street Journal column predicted the next day's market moves and volume (Tetlock, 2007), and lists
  built for finance read financial text far better than general-purpose ones, which call words like "liability",
  "tax" or "cost" negative (Loughran & McDonald, 2011). Counts are transparent and cost nothing to run.
- **Which lists.** The Loughran-McDonald lists are "free for use in academic research", with commercial licences by
  request. The lab is personal research aimed, one day, at real money, and its repository is public, so it neither
  downloads nor ships them. If the owner obtains them under terms that fit, placing their CSV
  (``Loughran-McDonald_MasterDictionary*.csv``) in ``<data>/radar/lexicon/`` makes the radar use them instead.
- **The default lists** below are the lab's own: about 200 words chosen for news headlines on markets, the economy,
  AI and conflict, in the Loughran-McDonald spirit (words whose tone in this setting is clear; ambiguous ones such as
  "record", "deal", "cut" or "high" left out). They are small and uncalibrated: a reading is a description of the
  headlines, not evidence of anything until a test says so.
- **Negation** follows Loughran & McDonald: a positive word within three words after "no", "not", "never" and the
  like counts as negative. Negative words are not flipped ("not a crash" is rare and still about a crash).
- **Untrusted text.** Headlines come from anywhere on the web. They are cleaned of control characters, cut to a
  fixed length, stored and shown as data. Nothing in them is ever executed, followed or passed on as an instruction.
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import config

NEGATIVE = frozenset("""
crash crashes crashed crashing plunge plunges plunged plunging slump slumps slumped slumping tumble tumbles tumbled
tumbling sink sinks sank sinking slide slides slid sliding selloff selloffs sell-off rout routs routed panic panics
panicked fear fears feared scare scares scared turmoil chaos recession recessions downturn downturns depression
crisis crises collapse collapses collapsed collapsing default defaults defaulted bankrupt bankruptcy bankruptcies
insolvent insolvency layoff layoffs fired loss losses lose loses losing lost decline declines declined declining
drop drops dropped dropping fall falls fell falling plummet plummets plummeted plummeting slowdown slowdowns
stagnation stagflation contraction shrink shrinks shrank shrinking weak weaker weakest weakness weakening worst worse
worsen worsens worsened worsening warn warns warned warning warnings threat threats threaten threatens threatened
attack attacks attacked war wars invasion invade invades invaded airstrike airstrikes missile missiles sanction
sanctions ban bans banned lawsuit lawsuits sue sues sued fraud scandal hack hacked breach breaches outage outages
shortage shortages deficit deficits downgrade downgrades downgraded halt halts halted suspend suspends suspended fail
fails failed failing failure failures bubble bubbles kill kills killed dead death deaths casualties violence conflict
conflicts escalate escalates escalated escalation bearish underperform underperforms slash slashes slashed jobless
unemployment delinquency delinquencies foreclosure foreclosures distress distressed contagion meltdown wipeout
""".split())

POSITIVE = frozenset("""
surge surges surged surging soar soars soared soaring rally rallies rallied rallying rebound rebounds rebounded
recover recovers recovered recovering recovery gain gains gained gaining jump jumps jumped jumping rise rises rose
rising climb climbs climbed climbing boom booms booming growth grow grows grew growing strong stronger strongest
strength strengthen strengthens beat beats upgrade upgrades upgraded profit profits profitable optimism optimistic
confidence confident boost boosts boosted improve improves improved improving improvement breakthrough breakthroughs
success successful win wins won ceasefire peace truce ease eases eased easing stabilize stabilizes stabilized stable
bullish outperform outperforms outperformed expand expands expanded expansion hire hires hiring upbeat resilient
resilience robust advance advances advanced
""".split())

UNCERTAIN = frozenset("""
uncertain uncertainty uncertainties volatile volatility risk risks risky doubt doubts doubtful unclear unknown
unpredictable speculation speculative turbulence turbulent jitters jittery
""".split())

NEGATORS = frozenset("no not never none neither nor nobody without isn't aren't wasn't weren't don't doesn't didn't "
                     "won't can't cannot".split())
NEGATION_WINDOW = 3
MAX_CHARS = 300
_WORD = re.compile(r"[a-z]+(?:['’-][a-z]+)?")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁦-⁩]")


def clean(text: Any) -> str:
    """Untrusted headline text made safe to store and show.

    Args:
        text: Anything; non-strings become empty.

    Returns:
        The text without control or direction-override characters, whitespace collapsed, at most 300 characters.
    """
    if not isinstance(text, str):
        return ""
    return " ".join(_CONTROL.sub(" ", text).split())[:MAX_CHARS]


def lexicon_files() -> list[Path]:
    """Loughran-McDonald master dictionaries the owner placed in the lexicon folder.

    Returns:
        Matching CSV files in ``<data>/radar/lexicon/``, newest name last.
    """
    folder = config.data_dir() / "radar" / "lexicon"
    return sorted(folder.glob("Loughran-McDonald_MasterDictionary*.csv")) if folder.exists() else []


@lru_cache(maxsize=4)
def _load_lm(path: str) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """The negative, positive and uncertainty words of a Loughran-McDonald master dictionary.

    Args:
        path: The CSV file (columns ``Word``, ``Negative``, ``Positive``, ``Uncertainty``; non-zero marks a member).

    Returns:
        The three word sets, lowercase.
    """
    neg, pos, unc = set(), set(), set()
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            word = (row.get("Word") or "").strip().lower()
            if not word:
                continue
            for col, bucket in (("Negative", neg), ("Positive", pos), ("Uncertainty", unc)):
                if (row.get(col) or "0").strip() not in ("", "0"):
                    bucket.add(word)
    return frozenset(neg), frozenset(pos), frozenset(unc)


def lexicon() -> dict[str, Any]:
    """The word lists in use.

    Returns:
        ``name`` (``lab-headlines-v1`` or the Loughran-McDonald file's name), ``negative``, ``positive`` and
        ``uncertain`` word sets.
    """
    files = lexicon_files()
    if files:
        neg, pos, unc = _load_lm(str(files[-1]))
        return {"name": files[-1].stem, "negative": neg, "positive": pos, "uncertain": unc}
    return {"name": "lab-headlines-v1", "negative": NEGATIVE, "positive": POSITIVE, "uncertain": UNCERTAIN}


def score(text: str, words: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tone of one headline.

    Args:
        text: The headline (already cleaned or not).
        words: Word lists (``lexicon()``); None loads them.

    Returns:
        ``pos`` and ``neg`` (counts after negation) and ``score``: (pos - neg) / (pos + neg), between -1 and 1, 0.0
        when the headline has no tone word.
    """
    lex = words or lexicon()
    tokens = _WORD.findall(clean(text).lower().replace("’", "'"))
    pos = neg = 0
    for i, tok in enumerate(tokens):
        if tok in lex["negative"]:
            neg += 1
        elif tok in lex["positive"]:
            if any(t in NEGATORS for t in tokens[max(0, i - NEGATION_WINDOW):i]):
                neg += 1
            else:
                pos += 1
    total = pos + neg
    return {"pos": pos, "neg": neg, "score": (pos - neg) / total if total else 0.0}


def net_tone(rows: list[dict[str, Any]]) -> float | None:
    """Net tone of many headlines: (all positive words - all negative words) / (all tone words).

    Args:
        rows: Dicts with ``pos`` and ``neg``.

    Returns:
        Between -1 and 1; None when no headline has a tone word.
    """
    pos = sum(int(r.get("pos") or 0) for r in rows)
    neg = sum(int(r.get("neg") or 0) for r in rows)
    return (pos - neg) / (pos + neg) if pos + neg else None
