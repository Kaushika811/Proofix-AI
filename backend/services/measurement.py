"""Tri-state measurement semantics.

The pipeline had one bug in many places: **"not measured" was spelled `0.0`.**

`A9` scoring four new security findings produces `security_score = 0.0` — a real
measurement of a bad outcome. `A9` never running leaves `state.security_result`
empty, and every consumer read it as `security.get("security_score", 0.0)` —
also `0.0`. The two are opposite facts and the code could not tell them apart,
so a run that skipped the security re-scan was reported as having *failed* it,
and that fabricated zero was averaged into the trust score that gates merges.

The three states this module distinguishes:

    measured    a number the pipeline actually computed. A measured 0.0 is a
                real, bad result and must keep participating in scoring.
    unmeasured  `None`. The measurement never happened — the agent was skipped,
                the environment blocked it, the stage never ran. It must never
                be coerced to a number, averaged, or compared to a threshold.
    failed      *a kind of measured.* A failure has a number and a reason, and
                it stays in the arithmetic. "Failed" is not "absent".

The rule everywhere downstream: **unmeasured values do not participate.** They
do not lower an average, they do not satisfy a threshold, and they do not
silently pass one either — a gate that requires evidence is not satisfied by the
absence of evidence.
"""

from __future__ import annotations

from typing import Iterable

Score = float | None


def is_measured(value: Score) -> bool:
    """True when a real measurement exists — including a measured zero."""
    return value is not None


def measured_only(values: Iterable[Score]) -> list[float]:
    """Drop the unmeasured. Measured zeros are kept: they are results."""
    return [v for v in values if v is not None]


def measured_mean(values: Iterable[Score]) -> Score:
    """Mean of the measurements that exist, or `None` when none do.

    Averaging over a fixed denominator was the specific arithmetic bug: four
    axes summed and divided by four, when only two had been measured, reported a
    run at half its true score and penalised it for work nobody performed.
    """
    present = measured_only(values)
    if not present:
        return None
    return sum(present) / len(present)


def meets_threshold(value: Score, threshold: float) -> bool:
    """Whether a measurement clears a bar.

    **Unmeasured never clears it.** A gate exists to require evidence, so the
    absence of evidence cannot satisfy it — but note the asymmetry with
    `below_threshold` below: unmeasured is not a *failure* either, and the two
    questions have different answers for the same input.
    """
    return value is not None and value >= threshold


def below_threshold(value: Score, threshold: float) -> bool:
    """Whether a measurement is present *and* fell short.

    Deliberately not the negation of `meets_threshold`. Both are false for an
    unmeasured value, and that is the point: an unmeasured axis neither clears
    the bar nor fails it. Ask this one when the answer becomes a user-facing
    accusation ("Low axis scores: security=0") — an axis that never ran has not
    scored badly, it has not scored, and saying otherwise was the visible half
    of this bug.
    """
    return value is not None and value < threshold


def format_score(value: Score, *, unit: str = "", places: int = 0) -> str:
    """Render for humans without inventing a number."""
    if value is None:
        return "not measured"
    return f"{value:.{places}f}{unit}"
