# Progress log

Dated entries, newest first. What's done, what's deferred, decisions
made. Read this before assuming anything about the module's current
state.

## 2026-08-30 — Module created from capability-module-template, first implementation

- Renamed the `capability/` package to `job_analyst/`; updated
  `pyproject.toml` (`name` → `job-analyst`, `packages`), `.importlinter`
  (`root_package`, `source_modules`), and the imports in `cli.py` /
  `tests/`.
- Wrote `docs/CONTRACT.md`. Takes a job posting (`posting`, plus
  optional `role_hint` / `count` / `expert_guidance`); returns a
  prioritised, posting-anchored requirements list on the
  `critical` / `high` / `medium` / `low` scale, a one–two sentence
  summary of what the employer is buying, 3–6 "reading between the
  lines" inferences, and a `Cost`. Perspective is fixed to the hiring
  manager / advertising company — no candidate data is an input.
  `Cost` matches `cover_letter_writer.Cost` / `cv_writer.Cost` exactly.
- Implemented `_contract.py` and `_core.py`:
  - one `claude-sonnet-5` call at `output_config={"effort": "low"}`,
    structured via a `record_analysis` tool (`tool_choice` auto; a JSON
    text reply is accepted as a fallback);
  - every quote verified as a verbatim substring of the posting —
    snapped to the closest real span with flexible whitespace/case, or
    dropped to `""`, never paraphrased;
  - importance coerced onto the four-value scale; requirements sorted
    `critical → high → medium → low` and capped at 18;
    `reading_between_the_lines` capped at 6;
  - frozen system prompt sent with `cache_control`; rate-card constants
    for `Cost.usd`.
- `cli.py` renders the analysis as Markdown to stdout, cost to stderr.
- `examples/sample.txt` is a full sample posting (Staff Data Engineer).
- `tests/test_run.py` — offline (`_core._generate` monkeypatched), one
  test per contract promise. `uv run pytest` and `uv run lint-imports`
  green.
- `.env.example` (tracked) + `.env` (gitignored) carry `ANTHROPIC_API_KEY`;
  `cli.py` calls `load_dotenv()` if `python-dotenv` (dev dep) is present.
  The key itself is not set yet — paste it into `.env` before a real run.

### Deferred

- Not yet tagged. Tag `v0.1.0` once a real posting through
  `uv run python cli.py --input examples/sample.txt` produces output
  worth using.
- Consumer (`automation-dashboard`) still imports a placeholder and
  refers to the old name `job-post-analyst`; switching it to a pinned
  `job-analyst` dep is dashboard-side work, tracked in that repo's plan.
