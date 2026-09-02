# Job Analyst

A standalone capability module. Given a job posting, it analyses the
posting **from the hiring manager's / advertising company's
perspective** — what that employer is really weighing when they read
applications — and returns a prioritised, posting-anchored list plus a
short read of the intent behind the advert. It works on its own and can
be composed into larger workflows later without changing.

> Part of a larger system. The rules every capability module follows are
> in `CLAUDE.md`. The input/output spec is `docs/CONTRACT.md`. The dated
> change log is `docs/PROGRESS.md`.

## What it does

You hand it a job posting as plain text. It reads the advert the way the
hiring manager who wrote it would, and returns:

- `requirements`: an ordered list (most important first), each with a
  `point` — a pronoun-free instruction to whatever will write a cover
  letter or CV — a verbatim `quote` from the posting it is anchored to,
  an `importance` (`critical` / `high` / `medium` / `low`), and a
  one-line `rationale`.
- `summary`: one or two sentences on what this employer is really
  buying.
- `company` / `job_title`: the hiring company and the role title, copied
  from the posting as written — `""` when the posting never states them.
- `reading_between_the_lines`: 3–6 short strings — the real seniority
  bar the wording implies, the failure the role is likely a reaction to,
  pace and culture cues. Inferences, not restatements.
- `cost`: token counts and an estimated dollar figure for the one LLM
  call.

It takes **no candidate data** — it characterises the role, it never
judges a person against it. It never fetches anything: every input
arrives as an argument. Scraping the posting, extracting text from a
PDF, and assessing a candidate's fit all happen elsewhere.

## Inputs

Required: `posting` (text).

Optional (full spec in `docs/CONTRACT.md`):

- `role_hint` — title / company, only if the posting text buries or
  omits them.
- `count` — target number of requirements (default 12; clamped to
  6..18).
- `expert_guidance` — free-text operator steering threaded into the
  prompt (e.g. "weight leadership signals harder for this client").

## Run it

Needs `uv` and an Anthropic API key in `.env` (`cp .env.example .env`,
then paste the key).

```
# bundled demo posting
uv run python cli.py --input examples/sample.txt

# your own file, or stdin
uv run python cli.py --input path/to/posting.txt
pbpaste | uv run python cli.py --count 8
```

The analysis prints as Markdown to stdout; the one-line cost estimate
goes to stderr. `uv run python cli.py -h` lists every flag
(`--role-hint`, `--count`, `--guidance`).

## Use it from Python

`run(Input(...))` is the whole public surface.

```python
from job_analyst import Input, run

result = run(Input(posting=open("posting.txt").read()))

for req in result.requirements:
    print(f"[{req.importance}] {req.point}")
    if req.quote:
        print(f"    ⤷ {req.quote}")
print(result.summary)
```

Also public: `Output`, `Requirement`, and `Cost` — the result shapes.

## Checks

```
uv run pytest          # proves run() honours docs/CONTRACT.md (offline; no API key needed)
uv run lint-imports    # fails if an orchestration framework sneaks in
```

## How it works

One `claude-sonnet-5` call at `effort="low"` does the analysis, returned
through a `record_analysis` tool call. Everything around it is prompt
assembly and coercion to the contract: every `quote` is verified against
the posting and snapped to the closest real span (or dropped to `""` —
never a paraphrase), `importance` is forced onto the four-value scale,
and `requirements` are sorted `critical → high → medium → low` and
capped at 18. The model id and prompt are internal details of
`_core.py`; callers never see or set them.

## How it composes

A consumer (an orchestrator, or the dashboard) imports `job_analyst.run`
and calls it, pinning this repo as a git tag:

```
job-analyst @ git+https://github.com/onlinemoose/job-analyst.git@vX.Y.Z
```

Nothing here reaches into the consumer. Typical use: the dashboard runs
`run(Input(posting=posting))`, turns each `Requirement` into an
annotation the user edits, shows `summary` and
`reading_between_the_lines` on the job page, and records `cost` against
the run.

## Layout

```
job_analyst/       the module: run(), Input, Output, Requirement, Cost
  _contract.py     the input/output shapes
  _core.py         one LLM call, prompt assembly, quote verification, coercion
cli.py             run it from a terminal
docs/
  CONTRACT.md      the input/output spec — written before the code
  PROGRESS.md      dated change log, newest first
examples/
  sample.txt       a full demo posting, so cli.py works end to end
tests/
  test_run.py      contract tests (LLM call stubbed — runs offline)
```

## Releasing a change

Consumers pin a git tag, so every change worth picking up is a tagged
release:

1. `uv run pytest` and `uv run lint-imports` pass.
2. Bump `version` in `pyproject.toml`: **patch** for a prompt tweak or
   fix, **minor** for a new optional input or better output, **major**
   if `docs/CONTRACT.md` changed in a way that breaks callers (a
   required input added, an output field reshaped, the `importance`
   scale changed).
3. Add a `docs/PROGRESS.md` entry, commit, `git tag vX.Y.Z`, push the
   tag.

Full detail in `CLAUDE.md` under "Releasing a new version".
