# job-analyst — Contract

> Written **before** implementation, per the capability-module rules
> (contract before code). If any of Inputs / Output below cannot be
> stated without naming another module, the boundary is wrong — stop and
> redraw it.

## Purpose

`job-analyst` reads a single job posting and analyses it **from the
hiring manager / advertising company's perspective** — not the
candidate's. It returns a prioritised list of what that employer is
really weighing when they read applications, each point anchored to a
verbatim span of the posting, plus a one–two sentence summary of what
the employer is actually buying and a short list of signals the posting
implies but never states outright.

No candidate data is an input. The capability never judges a person
against the role — it characterises the role. Fit assessment and gap
annotation happen elsewhere (in the dashboard workflow, the *user*
annotates gaps against this analysis).

## Front door

```python
def run(data: Input) -> Output: ...
```

The only public entry point. Everything else in the `job_analyst`
package is internal (`_core.py`, `_contract.py`). `run()` receives
everything it needs on `data`; it never fetches its own inputs.

## Input

Posting only — no CV, no candidate profile.

```python
@dataclass
class Input:
    posting: str                        # required — raw job posting text
    role_hint: str | None = None        # optional — title / company, if not obvious in the text
    count: int = 12                     # target number of requirements; clamped to 6..18
    expert_guidance: str | None = None  # optional — operator steering (matches the sibling capabilities)
```

- `posting` — the full advert as plain text. The caller has already
  scraped or pasted it; `job-analyst` does **not** take a URL and does
  **not** fetch anything.
- `role_hint` — used only to disambiguate when the posting text buries
  or omits the title/company. Never a substitute for `posting`.
- `count` — how many `requirements` to aim for. Values outside `6..18`
  are clamped, not rejected.
- `expert_guidance` — free-text operator instruction threaded into the
  prompt (e.g. "weight leadership signals harder for this client").
  Same field name and role as `cover_letter_writer` / `cv_writer`.

### Input validation

- `posting` empty or whitespace-only → `ValueError`.
- `posting` very short or clearly not prose → still best-effort; **no**
  raise. Output may have fewer `requirements` and a thinner
  `reading_between_the_lines`.

## Requirement

```python
@dataclass
class Requirement:
    point: str        # pronoun-free imperative instruction to whatever writes a letter or CV.
                      #   No "you", no "I". e.g.
                      #   "Lead with evidence of owning a platform migration end to end"
    quote: str        # the posting's OWN WORDS, in order, that this point is anchored to —
                      #   internal whitespace collapsed to single spaces, so it is always one
                      #   line. Verified after generation against `posting` (whitespace-
                      #   insensitive); if it cannot match, it is trimmed to the closest real
                      #   span, or set to "" — never a paraphrase.
    importance: str   # exactly one of: "critical" | "high" | "medium" | "low"
    rationale: str    # one line — why the hiring manager weights this
```

- `point` is written as a **neutral instruction to a downstream
  writer**, never as advice to a person. It contains no first- or
  second-person pronouns.
- `quote` is the posting's own words in order, whitespace collapsed to a
  single line (so it drops safely into a blockquote or a one-per-line
  format). It matches `Input.posting` word-for-word under that collapse,
  verified post-generation. It is `""` only when no faithful anchor
  exists.
- `importance` is exactly one of the four scale values — no other
  strings, no `None`. An off-scale value from the model is coerced to
  `"medium"`.

## Output

```python
@dataclass
class Output:
    requirements: list[Requirement]        # ordered, most important first; len tracks Input.count, within 6..18
    summary: str                           # 1–2 sentences: what this employer is really buying
    reading_between_the_lines: list[str]   # 3–6 short strings: signals the posting implies but
                                           #   never states — the real seniority bar, what they've
                                           #   likely been burned by, culture / pace cues
    cost: Cost
```

- `requirements` — sorted by `importance`
  (`critical` → `high` → `medium` → `low`), then by salience within a
  band. Capped at 18.
- `summary` — non-empty for any non-trivial posting; a plain-language
  read of the employer's actual intent.
- `reading_between_the_lines` — up to 6 entries (the model is asked for
  3–6). Inferences, explicitly *not* restatements of posting text.
- `cost` — see below.

## Cost

Identical shape to `cover_letter_writer.Cost` / `cv_writer.Cost`, so a
consumer's run-metadata wiring is a copy:

```python
@dataclass
class Cost:
    usd: float
    input_tokens: int
    output_tokens: int          # includes thinking tokens
    cache_read_input_tokens: int
    cache_write_input_tokens: int
```

`usd` is computed inside `_core.py` from the model's published per-1M
rates and the `usage` block on the Anthropic response.

## Behaviour and guarantees

- **Fixed perspective.** Always the hiring manager + advertising
  company, never the candidate. There is no candidate input and no way
  to ask for a candidate-side reading.
- **Pronoun-free points.** Every `Requirement.point` is an imperative
  with no "you" / "I" — a neutral instruction to whatever writes the
  letter or CV.
- **Ordered output.** `requirements` is sorted
  `critical` → `high` → `medium` → `low`, then by salience.
- **Anchored quotes.** Every `Requirement.quote` is the posting's own
  words in order (whitespace collapsed to one line), verified after
  generation; unmatchable quotes are trimmed to the nearest real span
  or set to `""` — never paraphrased.
- **Stable shape, near-stable content.** The same posting yields the
  same output shape and near-identical content run to run. Not
  byte-stable.
- **Pure.** No network except the LLM call. No filesystem. No storage.
  No environment beyond `ANTHROPIC_API_KEY` (plus the standard Anthropic
  SDK variables).
- **No orchestration framework.** `prefect` / `dagster` / `airflow` /
  `celery` are not dependencies; `uv run lint-imports` enforces this.

## Storage

None.

## Model and inference (implementation note)

Guidance for `_core.py`, not part of the contract surface — callers
never see or set the model.

- **Model: `claude-sonnet-5`** ($2 / $10 per 1M input / output, 1M
  context). Ample for structured extraction plus ranking, and the
  cheapest current Sonnet.
- **Effort:** `output_config={"effort": "low"}`. On `claude-sonnet-5`
  the model still thinks adaptively at this setting; `thinking` is not
  passed explicitly. Raise to `"medium"` only if ranking quality or the
  between-the-lines inferences measurably need it.
- The model id is pinned in `_core.py`. **Do not** add a `model`
  parameter to `Input`.
- The structured shape comes from a single `record_analysis` tool call
  (`tool_choice` auto, with a firm system instruction). A JSON text
  reply is accepted as a fallback. Either way, `quote` values are
  verified against `Input.posting` before `Output` is built.
- `Cost.usd` = `input_tokens / 1e6 * 2.00 + output_tokens / 1e6 * 10.00`
  plus the cache-rate adjustments (`0.20` / `2.50` per 1M for
  read / write), taken from `response.usage`. `output_tokens` includes
  thinking tokens.
- The system prompt is a frozen constant and is sent with
  `cache_control` so clustered runs re-read it cheaply.

## Public surface

```python
# job_analyst/__init__.py
from ._contract import Cost, Input, Output, Requirement
from ._core import run

__all__ = ["run", "Input", "Output", "Requirement", "Cost"]
```

Nothing else is public. `_core.py` and `_contract.py` are internal.

## Usage example

Input (posting excerpt):

```
Senior Backend Engineer — Platform team. You will own the migration of
our monolith to services, lead on-call for critical payment
infrastructure, and mentor two junior engineers. Must have 6+ years
building high-throughput systems in Go or Java and deep Postgres
experience. Nice to have: Kubernetes, Kafka, a fintech background.
High-pace environment - we move fast and expect ownership.
```

```python
from job_analyst import Input, run

out = run(Input(posting=POSTING_TEXT))
```

Output (shown as pseudo-JSON):

```python
Output(
    requirements=[
        Requirement(
            point="Lead with evidence of owning a monolith-to-services migration end to end",
            quote="own the migration of our monolith to services",
            importance="critical",
            rationale="The role's headline deliverable — they are buying migration leadership.",
        ),
        Requirement(
            point="Show sustained years building high-throughput systems in Go or Java",
            quote="6+ years building high-throughput systems in Go or Java",
            importance="critical",
            rationale="Stated as a hard minimum; the main screen-out gate.",
        ),
        Requirement(
            point="Demonstrate carrying production on-call for payment-critical infrastructure",
            quote="lead on-call for critical payment infrastructure",
            importance="high",
            rationale="Signals prior reliability pain on money-movement systems.",
        ),
        Requirement(
            point="Give concrete examples of mentoring junior engineers",
            quote="mentor two junior engineers",
            importance="medium",
            rationale="Team-shaped need and a seniority signal, but not a gate.",
        ),
    ],
    summary=(
        "A platform team buying senior migration leadership for a "
        "payments-critical backend, under real delivery pressure."
    ),
    reading_between_the_lines=[
        "The real seniority bar is staff-adjacent: someone who has run a migration, not just contributed to one.",
        "On-call for payments plus 'we move fast' suggests recent reliability or delivery pain they are hiring to fix.",
        "Go or Java is named explicitly while Kubernetes and Kafka are only 'nice to have' — language and systems fluency outranks infra depth.",
        "Two named juniors implies a junior-heavy team that needs a stabilising senior.",
    ],
    cost=Cost(
        usd=0.0148,
        input_tokens=1820,
        output_tokens=1120,
        cache_read_input_tokens=0,
        cache_write_input_tokens=0,
    ),
)
```

## Errors

- `ValueError` — `posting` empty or whitespace-only.
- `RuntimeError` — the model returned neither a `record_analysis` tool
  call nor a JSON object.
- Anthropic SDK exceptions (`anthropic.AuthenticationError`,
  `anthropic.RateLimitError`, `anthropic.APIStatusError`, …) propagate
  **unwrapped**. The capability does not catch or re-raise them.

## Testing expectations (for the repo)

- **Contract test** — `run()` on a vendored example payload returns:
  - every `importance` in `{"critical", "high", "medium", "low"}`;
  - every non-empty `quote` matches the posting word-for-word
    (whitespace-insensitive) and is a single line;
  - `requirements` is sorted by importance and capped at 18;
  - `summary` non-empty;
  - `len(reading_between_the_lines) <= 6`.
- **`ValueError`** on empty / whitespace `posting`.
- **Offline** — the LLM call (`_core._generate`) is monkeypatched, so
  the suite runs green with no `ANTHROPIC_API_KEY`, the way a consumer
  stubs `run` in its own tests.
- `uv run pytest` and `uv run lint-imports` both green before any tag.

## Versioning and release

SemVer tags `vX.Y.Z`; consumed only as a pinned git dependency
(`job-analyst @ git+https://github.com/onlinemoose/job-analyst.git@vX.Y.Z`).

- **patch** — prompt tweak or fix; Input/Output unchanged.
- **minor** — new *optional* input, or better output; existing callers
  unaffected.
- **major** — this contract changed in a way that breaks callers (a
  required input added, an Output field removed or reshaped, the
  `importance` scale changed).

Every tagged change gets a `docs/PROGRESS.md` entry. Nothing here
reaches into a consumer; it moves its own pin.

## How a dashboard consumes it (informational)

- `uv add "job-analyst @ git+https://github.com/onlinemoose/job-analyst.git@vX.Y.Z"`
- The analysis step calls `run(Input(posting=posting))` and maps the
  result into its own shapes: `Requirement.point` + `Requirement.quote`
  → an emphasis/annotation pair; `reading_between_the_lines` renders
  under `summary` on the job detail view; `Cost` feeds the run's
  metadata.
- The `importance` scale is `critical` / `high` / `medium` / `low`.
