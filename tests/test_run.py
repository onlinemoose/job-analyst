"""Proves run() honours the contract in docs/CONTRACT.md.

The LLM call is stubbed (`_core._generate`) so these run offline with no
ANTHROPIC_API_KEY. One test per promise the module makes.
"""

from types import SimpleNamespace

import pytest

from job_analyst import Cost, Input, Output, Requirement, run
from job_analyst import _core

POSTING = """\
Senior Backend Engineer — Platform team.

You will own the migration of our monolith to services, lead on-call for
critical payment infrastructure, and mentor two junior engineers. Must
have 6+ years building high-throughput systems in Go or Java and deep
Postgres experience. Nice to have: Kubernetes, Kafka, a fintech
background. High-pace environment - we move fast and expect ownership.
"""


def _payload(**overrides) -> dict:
    base = {
        "requirements": [
            {
                "point": "Show measurable outcomes from a past monolith-to-services migration",
                "quote": "own the migration of our monolith to services",  # loose (verb tense)
                "importance": "medium",
                "rationale": "The role's headline deliverable.",
            },
            {
                "point": "Lead with sustained years building high-throughput systems in Go or Java",
                "quote": "6+ years building high-throughput systems in Go or Java",  # verbatim
                "importance": "critical",
                "rationale": "Stated as a hard minimum; the main screen-out gate.",
            },
            {
                "point": "Give evidence of carrying production on-call for payment-critical systems",
                "quote": "lead on-call for critical payment infrastructure",  # verbatim
                "importance": "high",
                "rationale": "Signals prior reliability pain on money-movement systems.",
            },
            {
                "point": "Provide concrete examples of mentoring junior engineers",
                "quote": "a paraphrase that appears nowhere in the advert",  # unmatchable
                "importance": "low",
                "rationale": "Team-shaped need, not a gate.",
            },
        ],
        "summary": (
            "A platform team buying senior migration leadership for a "
            "payments-critical backend, under real delivery pressure."
        ),
        "reading_between_the_lines": [
            "The real seniority bar is staff-adjacent: someone who has run a migration, not just helped.",
            "Payments on-call plus 'we move fast' hints at recent reliability or delivery pain.",
            "Go or Java is named while Kubernetes and Kafka are only nice-to-have — language fluency outranks infra depth.",
        ],
    }
    base.update(overrides)
    return base


STUB_COST = Cost(
    usd=0.0148,
    input_tokens=1820,
    output_tokens=1120,
    cache_read_input_tokens=0,
    cache_write_input_tokens=200,
)


@pytest.fixture
def stub_llm(monkeypatch):
    """Capture the system + prompt sent to the model; return a canned payload."""
    seen: dict = {}

    def fake_generate(system: str, prompt: str) -> tuple[dict, Cost]:
        seen["system"] = system
        seen["prompt"] = prompt
        return _payload(), STUB_COST

    monkeypatch.setattr(_core, "_generate", fake_generate)
    return seen


def test_run_returns_the_output_type(stub_llm):
    result = run(Input(posting=POSTING))
    assert isinstance(result, Output)
    assert all(isinstance(r, Requirement) for r in result.requirements)
    assert isinstance(result.summary, str)
    assert isinstance(result.reading_between_the_lines, list)
    assert isinstance(result.cost, Cost)


def test_works_with_only_the_required_input(stub_llm):
    # Rule 7: a valid result from `posting` alone.
    result = run(Input(posting=POSTING))
    assert result.requirements
    assert result.summary != ""


def test_the_posting_reaches_the_model(stub_llm):
    run(Input(posting=POSTING))
    assert "own the migration of our monolith to services" in stub_llm["prompt"]


def test_importance_is_forced_onto_the_scale(stub_llm, monkeypatch):
    payload = _payload()
    payload["requirements"][0]["importance"] = "must-have"  # off-scale value from the model
    monkeypatch.setattr(_core, "_generate", lambda s, p: (payload, STUB_COST))

    result = run(Input(posting=POSTING))
    assert {r.importance for r in result.requirements} <= set(_core.IMPORTANCE)
    # the off-scale one is coerced, not dropped
    assert any("measurable outcomes" in r.point for r in result.requirements)


def test_requirements_are_sorted_by_importance(stub_llm):
    result = run(Input(posting=POSTING))
    ranks = [_core._IMPORTANCE_RANK[r.importance] for r in result.requirements]
    assert ranks == sorted(ranks)
    assert result.requirements[0].importance == "critical"


def test_quotes_are_the_postings_own_words_or_empty(stub_llm):
    result = run(Input(posting=POSTING))
    for req in result.requirements:
        if req.quote:
            assert _core._matches_posting(req.quote, POSTING), (
                f"quote is not the posting's own words: {req.quote!r}"
            )
            assert "\n" not in req.quote, "quote must be a single line"
    # the unmatchable paraphrase collapses to ""
    mentor = next(r for r in result.requirements if "mentoring" in r.point)
    assert mentor.quote == ""


def test_multiline_span_is_collapsed_to_one_line(stub_llm, monkeypatch):
    # This span wraps across a line break in POSTING ("on-call for\ncritical").
    assert "on-call for\ncritical payment infrastructure" in POSTING
    payload = _payload(
        requirements=[
            {
                "point": "Evidence of carrying payments on-call",
                "quote": "lead on-call for critical payment infrastructure",
                "importance": "critical",
                "rationale": "x",
            }
        ]
    )
    monkeypatch.setattr(_core, "_generate", lambda s, p: (payload, STUB_COST))
    quote = run(Input(posting=POSTING)).requirements[0].quote
    assert "\n" not in quote
    assert quote == "lead on-call for critical payment infrastructure"
    assert _core._matches_posting(quote, POSTING)


def test_loose_quote_is_snapped_to_the_real_span(stub_llm, monkeypatch):
    payload = _payload(
        requirements=[
            {
                "point": "Own a migration end to end",
                "quote": "OWN   THE   MIGRATION of our monolith",  # case + whitespace differ
                "importance": "critical",
                "rationale": "x",
            }
        ]
    )
    monkeypatch.setattr(_core, "_generate", lambda s, p: (payload, STUB_COST))
    result = run(Input(posting=POSTING))
    assert result.requirements[0].quote == "own the migration of our monolith"
    assert _core._matches_posting(result.requirements[0].quote, POSTING)


def test_summary_and_reading_between_the_lines(stub_llm):
    result = run(Input(posting=POSTING))
    assert result.summary
    assert 3 <= len(result.reading_between_the_lines) <= 6
    assert all(isinstance(s, str) and s for s in result.reading_between_the_lines)


def test_reading_between_the_lines_is_capped_at_six(stub_llm, monkeypatch):
    payload = _payload(reading_between_the_lines=[f"signal {i}" for i in range(12)])
    monkeypatch.setattr(_core, "_generate", lambda s, p: (payload, STUB_COST))
    result = run(Input(posting=POSTING))
    assert len(result.reading_between_the_lines) == _core.RBTL_MAX


def test_requirements_are_capped_at_the_upper_bound(stub_llm, monkeypatch):
    many = [
        {
            "point": f"Point number {i}",
            "quote": "",
            "importance": "medium",
            "rationale": "",
        }
        for i in range(25)
    ]
    monkeypatch.setattr(_core, "_generate", lambda s, p: (_payload(requirements=many), STUB_COST))
    result = run(Input(posting=POSTING))
    assert len(result.requirements) == _core.COUNT_MAX


def test_count_is_clamped_into_the_prompt(stub_llm):
    run(Input(posting=POSTING, count=2))
    assert f"Aim for about {_core.COUNT_MIN}." in stub_llm["prompt"]

    run(Input(posting=POSTING, count=99))
    assert f"Aim for about {_core.COUNT_MAX}." in stub_llm["prompt"]


def test_role_hint_and_guidance_are_optional_and_passed_through(stub_llm):
    run(Input(posting=POSTING))
    assert "## Role hint" not in stub_llm["prompt"]
    assert "## Operator guidance" not in stub_llm["prompt"]

    run(
        Input(
            posting=POSTING,
            role_hint="Staff Backend Engineer, Northwind",
            expert_guidance="Weight leadership signals harder for this client.",
        )
    )
    prompt = stub_llm["prompt"]
    assert "Staff Backend Engineer, Northwind" in prompt
    assert "Weight leadership signals harder for this client." in prompt


def test_system_prompt_fixes_the_perspective(stub_llm):
    run(Input(posting=POSTING))
    system = stub_llm["system"]
    assert "hiring manager" in system
    assert "never the candidate's" in system


def test_output_carries_the_call_cost(stub_llm):
    result = run(Input(posting=POSTING))
    assert result.cost.usd == STUB_COST.usd
    assert result.cost.output_tokens == 1120
    assert result.cost.cache_write_input_tokens == 200


def test_empty_posting_is_rejected(stub_llm):
    with pytest.raises(ValueError):
        run(Input(posting="   \n  "))


# --- pricing (does not touch the network) -----------------------------------


def test_price_uses_the_rate_card():
    usage = SimpleNamespace(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
        cache_creation_input_tokens=1_000_000,
    )
    cost = _core._price(usage)
    # Sonnet 5 rate card: $2 in + $10 out + $0.20 cache-read + $2.50 cache-write.
    assert cost.usd == pytest.approx(14.70)
    assert cost.input_tokens == 1_000_000
    assert cost.cache_write_input_tokens == 1_000_000


def test_price_tolerates_missing_cache_fields():
    usage = SimpleNamespace(input_tokens=100, output_tokens=50)
    cost = _core._price(usage)
    assert cost.cache_read_input_tokens == 0
    assert cost.cache_write_input_tokens == 0
    assert cost.usd == pytest.approx(100 * 2e-6 + 50 * 10e-6)


# --- payload extraction ----------------------------------------------------


def test_extract_payload_prefers_the_tool_call():
    tool_block = SimpleNamespace(type="tool_use", name="record_analysis", input={"summary": "x"})
    message = SimpleNamespace(content=[tool_block], stop_reason="tool_use")
    assert _core._extract_payload(message) == {"summary": "x"}


def test_extract_payload_falls_back_to_json_text():
    text_block = SimpleNamespace(type="text", text='```json\n{"summary": "y"}\n```')
    message = SimpleNamespace(content=[text_block], stop_reason="end_turn")
    assert _core._extract_payload(message) == {"summary": "y"}


def test_extract_payload_raises_when_there_is_nothing_structured():
    text_block = SimpleNamespace(type="text", text="I can't help with that.")
    message = SimpleNamespace(content=[text_block], stop_reason="end_turn")
    with pytest.raises(RuntimeError):
        _core._extract_payload(message)
