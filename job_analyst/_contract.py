"""The shapes of what goes in and what comes out.

A "type" here just means: the written, named shape of a piece of data —
so the computer (and the next reader) can see exactly what's expected,
instead of everything passing loose bags of values around.

Keep this file small and readable; it mirrors docs/CONTRACT.md. If you
want incoming data validated automatically, swap these dataclasses for
Pydantic models — the rest of the module doesn't change.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Input:
    """Everything run() needs to analyse one job posting.

    Required field first, optional after. Every optional field has a
    default; the module produces a valid analysis from `posting` alone.
    """

    # --- required ---
    posting: str
    """The full advert, as raw text (title, company, responsibilities,
    requirements — whatever the posting contains). The caller has already
    scraped or pasted it; this module never takes a URL and never fetches
    anything."""

    # --- optional: must have a default; the module works without them ---
    role_hint: str | None = None
    """Title / company, used only to disambiguate when the posting text
    buries or omits them. Never a substitute for `posting`."""

    count: int = 12
    """Target number of requirements to return. Values outside 6..18 are
    clamped, not rejected."""

    expert_guidance: str | None = None
    """Free-text operator steering threaded into the prompt (e.g. "weight
    leadership signals harder for this client"). Supplied by whoever
    operates the module, not by a candidate. Same field name and role as
    cover_letter_writer / cv_writer."""


@dataclass
class Requirement:
    """One thing the employer is weighing, anchored to the posting.

    `point` is a pronoun-free imperative instruction to whatever writes a
    letter or CV — no "you", no "I". `quote` is a verbatim span of the
    posting the point is anchored to (a substring of `Input.posting`,
    verified after generation; "" when no faithful anchor exists — never
    a paraphrase). `importance` is exactly one of "critical" | "high" |
    "medium" | "low". `rationale` is one line on why the hiring manager
    weights this.
    """

    point: str
    quote: str
    importance: str
    rationale: str


@dataclass
class Cost:
    """What the single LLM call behind one analysis cost.

    The token counts are exact, taken straight from the API response.
    `usd` is those counts priced against the model's published rate
    card, frozen as constants in `_core.py` — a close estimate for
    budgeting and observability, not a billing record.
    """

    usd: float
    """Estimated dollar cost of the call."""

    input_tokens: int
    """Uncached prompt tokens, billed at the full input rate."""

    output_tokens: int
    """Completion tokens. Includes the model's internal thinking tokens,
    which also bill at the output rate."""

    cache_read_input_tokens: int
    """Prompt tokens served from the cache, billed at ~0.1x input."""

    cache_write_input_tokens: int
    """Prompt tokens written to the cache, billed at ~1.25x input."""


@dataclass
class Output:
    """Everything run() hands back."""

    requirements: list[Requirement]
    """Ordered, most important first (critical → high → medium → low,
    then by salience). Length tracks `Input.count`, always within
    6..18."""

    summary: str
    """One–two sentences: what this employer is really buying."""

    company: str
    """The hiring company's name, copied from the posting. "" when the
    posting never names it."""

    job_title: str
    """The role / job title, copied from the posting. "" when the posting
    never states it plainly."""

    reading_between_the_lines: list[str]
    """3–6 short strings: signals the posting implies but never states —
    the real seniority bar, the failure the role is likely a reaction
    to, pace / culture cues, team shape. Inferences, not restatements."""

    cost: Cost
    """What the LLM call cost — token counts and an estimated dollar
    figure. Present on every result; for observability and budgeting."""
