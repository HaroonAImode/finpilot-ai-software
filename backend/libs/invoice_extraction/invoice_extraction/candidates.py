"""P1 — shared deterministic candidate-selection primitives
(docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md §6's finding:
vendor/total/document_type/category each independently reinvent "which
candidate wins," with three genuinely different, inconsistent strategies
and no shared abstraction between them).

This module is deliberately small and contains ZERO field-specific logic —
no `if field_name == "vendor"` anywhere here, ever, and no knowledge of
what a vendor, a date, or a total looks like. A field module (fields.py,
and in future date/total-adjacent work) builds `Candidate` objects from its
own domain knowledge — its own label-anchoring, its own noise filters, its
own positional heuristics — then hands them to `select_candidate()` here.
This module never reaches back into a field's own extraction logic; the
boundary is one-directional.

Every contribution to a candidate's score is a NAMED, signed entry in its
own `evidence` dict — never a single opaque number computed once and
thrown away. This is what makes "why did this candidate win?" answerable
after the fact (the exact requirement the audit's §6/§7 asked for), and it
is also what makes negative evidence (§10 of the audit) nothing special: a
penalty is just a negative float in the same dict a positive signal lives
in, so there is no separate penalty mechanism to keep in sync with the
positive one.

Deliberately NOT a rule engine: there is no registry of rules, no plugin
system, no `if`/`elif` chain over field names anywhere in this file. A
caller decides what evidence exists and how much each signal is worth for
its own field; this module only knows how to sum evidence into a score and
pick a winner from a list of already-scored candidates, with an explicit
ambiguity check in between.
"""
from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass(frozen=True)
class Candidate:
    """One possible value for one field, with every reason it might or
    might not be right recorded as a named, signed contribution to
    `evidence` — never a single black-box score assigned directly. `score`
    is a derived property, computed fresh from `evidence` every time, so it
    can never drift out of sync with the reasons behind it.
    """

    field_name: str
    value: str
    #: Which extraction path produced this candidate — the same
    #: "label_anchor+pattern" / "positional_fallback" / etc. vocabulary
    #: confidence.py's METHOD_TIERS already uses, so a Candidate's method
    #: stays directly usable by the existing confidence-tiering mechanism
    #: without translation.
    method: str
    page: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    #: signal_name -> signed contribution. A negative entry is negative
    #: evidence (the audit's §10) — there is no separate penalty type; it
    #: is simply a name and a negative number living in the same dict as
    #: every positive signal.
    evidence: dict[str, float] = field(default_factory=dict)
    #: Free-form, human-readable provenance for debugging/explainability
    #: only (e.g. the raw OCR line this candidate came from, before
    #: cleanup) — NEVER read by scoring or selection, so a field module can
    #: stash anything here without risk of it silently influencing which
    #: candidate wins.
    context: dict[str, object] = field(default_factory=dict)

    @property
    def score(self) -> float:
        return sum(self.evidence.values())


@dataclass(frozen=True)
class SelectionResult:
    """The outcome of choosing among a field's own candidates, plus enough
    to explain why. `candidates` holds every candidate considered, sorted
    strongest-first — a review UI or a future audit needs this to answer
    "why did this one win over that one?", not just the bare winner.
    """

    winner: Optional[Candidate]
    #: "accept" — a clear winner with adequate separation from its nearest
    #:   rival (or the only candidate at all, so there was no rival to be
    #:   ambiguous against).
    #: "review" — a winner exists but its margin over the runner-up did not
    #:   clear the caller-supplied `ambiguity_margin`; the audit's §10 "two
    #:   candidates with insufficient evidence" case. This module still
    #:   names a winner (a caller that already returns "always something,
    #:   never nothing" — e.g. vendor's own established, tested behavior —
    #:   can keep doing so) but the caller must not treat "review" as
    #:   equivalent to "accept" when deciding how confidently to report it.
    #: "unknown" — no candidates existed at all.
    status: Literal["accept", "review", "unknown"]
    #: winner.score - runner_up.score. None when fewer than two candidates
    #: existed (there was nothing to be ambiguous against, not "perfectly
    #: unambiguous" — callers must not read a None margin as a strong
    #: signal either way).
    margin: Optional[float]
    candidates: list[Candidate]


def select_candidate(candidates: list[Candidate], *, ambiguity_margin: float) -> SelectionResult:
    """The one shared selection mechanism every field-specific candidate
    generator feeds into.

    Deliberately NOT "highest score wins, full stop" (the P1 task's own
    explicit requirement): a candidate that beats its nearest rival by only
    a hair is exactly as much a sign the evidence couldn't confidently
    separate them as it is a sign of a real winner. `ambiguity_margin` is
    the caller's own field-specific judgment of how much separation counts
    as "confident enough" — this function holds no opinion on what a good
    margin is for any particular field, only how to apply one once given.

    No candidates at all -> "unknown" (nothing to select from — this is a
    availability question, not an ambiguity question). Exactly one
    candidate -> "accept" unconditionally: there is no rival for it to be
    ambiguous against, and whether a single weak candidate should be
    trusted is a confidence-tier question for the caller (via that
    candidate's own evidence/score), never something this function invents
    an opinion about. Two or more candidates -> "accept" only if the top
    score clears the next-best by at least `ambiguity_margin`; otherwise
    "review".
    """
    if not candidates:
        return SelectionResult(winner=None, status="unknown", margin=None, candidates=[])
    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    if len(ranked) == 1:
        return SelectionResult(winner=ranked[0], status="accept", margin=None, candidates=ranked)
    margin = ranked[0].score - ranked[1].score
    status: Literal["accept", "review"] = "accept" if margin >= ambiguity_margin else "review"
    return SelectionResult(winner=ranked[0], status=status, margin=margin, candidates=ranked)
