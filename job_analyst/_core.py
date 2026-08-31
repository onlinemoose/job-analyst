"""Internal implementation. Nothing here is public — callers only ever
touch job_analyst.run().

The job: given an Input, produce an Output. One LLM call does the work;
everything around it is prompt assembly, pulling the structured reply
out of a tool call, and verifying every quote against the posting.
"""

from __future__ import annotations

import json
import logging
import re

import anthropic

from ._contract import Cost, Input, Output, Requirement

_log = logging.getLogger("job_analyst")

MODEL = "claude-sonnet-5"
MAX_TOKENS = 16_000

# Analysing a posting is structured extraction plus ranking, not a hard
# reasoning task. "low" effort keeps the model's thinking budget modest
# — thinking tokens bill at the output rate. On claude-sonnet-5 the
# model still thinks adaptively at this setting. Raise to "medium" if
# the ranking or the between-the-lines inferences start to slip.
EFFORT = "low"

# USD per token for MODEL, from Anthropic's published rate card. Update
# these whenever MODEL changes or the prices move — they are the only
# thing that makes Cost.usd more than a guess. Cache reads bill at ~0.1x
# the input rate; 5-minute cache writes at ~1.25x.
_USD_PER_INPUT_TOKEN = 2.00 / 1_000_000
_USD_PER_OUTPUT_TOKEN = 10.00 / 1_000_000
_USD_PER_CACHE_READ_TOKEN = 0.20 / 1_000_000
_USD_PER_CACHE_WRITE_TOKEN = 2.50 / 1_000_000

IMPORTANCE = ("critical", "high", "medium", "low")
_IMPORTANCE_RANK = {name: i for i, name in enumerate(IMPORTANCE)}

COUNT_MIN, COUNT_MAX = 6, 18
RBTL_MAX = 6

# Shortest fuzzy span (in whitespace-separated tokens) we will accept
# when a quote doesn't match the posting verbatim. Below this, a match
# is more likely coincidence than a real anchor.
_MIN_FUZZY_TOKENS = 3

SYSTEM_PROMPT = """\
You analyse a single job posting from the perspective of the hiring \
manager and the company that advertised it — never the candidate's. You \
do not judge any person against the role; you characterise the role: \
what this employer is really weighing when they read applications.

Call the `record_analysis` tool exactly once with your complete \
analysis. Do not reply with anything else.

Rules for the fields you record:

- `requirements`: the things the employer is weighing, most important \
  first. Aim for the number you are asked for.
  - `point` is a pronoun-free imperative instruction to whatever will \
    write a cover letter or CV. No "you", no "I". Write \
    "Lead with evidence of owning a platform migration end to end", \
    not "You should show that you have led a migration".
  - `quote` is a span copied VERBATIM from the posting — the exact text \
    this point is anchored to. Copy it character for character. If no \
    single span anchors the point, use an empty string rather than a \
    paraphrase.
  - `importance` is exactly one of: "critical" (a hard gate — the \
    application is screened out without it), "high" (weighted heavily), \
    "medium" (matters, not decisive), "low" (a tiebreaker).
  - `rationale` is one line on why the hiring manager weights this.
- `summary`: one or two sentences on what this employer is really \
  buying — the intent behind the advert, in plain language.
- `reading_between_the_lines`: 3 to 6 short strings. Inferences the \
  posting invites but does not state outright — the real seniority bar \
  the wording implies, the failure the role is likely a reaction to, \
  pace and culture cues, the shape of the team. Not restatements of \
  posting text.
"""

_ANALYSIS_TOOL: anthropic.types.ToolParam = {
    "name": "record_analysis",
    "description": (
        "Record the structured analysis of the job posting. Call this "
        "exactly once, with the whole analysis."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "requirements": {
                "type": "array",
                "description": "Prioritised, most important first.",
                "items": {
                    "type": "object",
                    "properties": {
                        "point": {
                            "type": "string",
                            "description": (
                                "Pronoun-free imperative instruction to a "
                                "letter / CV writer. No 'you', no 'I'."
                            ),
                        },
                        "quote": {
                            "type": "string",
                            "description": (
                                "A span copied verbatim from the posting that "
                                "this point is anchored to, or '' if none does."
                            ),
                        },
                        "importance": {
                            "type": "string",
                            "enum": list(IMPORTANCE),
                        },
                        "rationale": {
                            "type": "string",
                            "description": "One line: why the employer weights this.",
                        },
                    },
                    "required": ["point", "quote", "importance", "rationale"],
                },
            },
            "summary": {
                "type": "string",
                "description": "1-2 sentences: what this employer is really buying.",
            },
            "reading_between_the_lines": {
                "type": "array",
                "description": (
                    "3-6 short strings: signals the posting implies but does "
                    "not state."
                ),
                "items": {"type": "string"},
            },
        },
        "required": ["requirements", "summary", "reading_between_the_lines"],
    },
}


def run(data: Input) -> Output:
    """The one front door. Given an Input, return an Output.

    Does not read files, databases, or other modules — everything it
    needs is in `data`. The only outbound call is to the LLM used
    internally.
    """
    _validate(data)
    payload, cost = _generate(SYSTEM_PROMPT, _render_prompt(data))
    return _assemble(_normalise_payload(payload), cost, data.posting)


def _validate(data: Input) -> None:
    if not data.posting.strip():
        raise ValueError("posting is empty")


def _clamp_count(n: int) -> int:
    return max(COUNT_MIN, min(COUNT_MAX, n))


def _render_prompt(data: Input) -> str:
    parts: list[str] = []

    hint = (data.role_hint or "").strip()
    if hint:
        parts.append(f"## Role hint (only if the posting doesn't make it obvious)\n\n{hint}")

    count = _clamp_count(data.count)
    parts.append(
        "## How many requirements\n\n"
        f"Aim for about {count}. Never fewer than {COUNT_MIN}, never more than {COUNT_MAX}."
    )

    guidance = (data.expert_guidance or "").strip()
    if guidance:
        parts.append(f"## Operator guidance\n\n{guidance}")

    parts.append(f"## Job posting\n\n{data.posting.strip()}")

    return "\n\n".join(parts)


def _generate(system: str, prompt: str) -> tuple[dict, Cost]:
    """The one outbound call: the LLM this module uses internally.

    Returns the tool-call payload (a plain dict) and what the call cost.
    The cost is also logged at INFO on the `job_analyst` logger, so it
    can be seen without threading the Output all the way back.
    """
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": EFFORT},
        # The system prompt is identical on every call; caching it means
        # runs that cluster (a batch of postings) re-read the fixed
        # prefix at a fraction of the input price.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": prompt}],
        tools=[_ANALYSIS_TOOL],
        tool_choice={"type": "auto"},
    )
    payload = _extract_payload(message)
    cost = _price(message.usage)
    _log.info(
        "posting analysed on %s (effort=%s): $%.4f est. — "
        "%d input, %d output, %d cache-read, %d cache-write tokens",
        MODEL,
        EFFORT,
        cost.usd,
        cost.input_tokens,
        cost.output_tokens,
        cost.cache_read_input_tokens,
        cost.cache_write_input_tokens,
    )
    return payload, cost


def _extract_payload(message: anthropic.types.Message) -> dict:
    """Pull the record_analysis input out of the reply.

    Preferred path: a tool_use block. Fallback: a text block that is
    itself a JSON object (models occasionally answer in prose despite
    the instruction). Anything else is a hard error — better to fail
    loudly than to return an empty analysis.
    """
    for block in message.content:
        if block.type == "tool_use" and block.name == _ANALYSIS_TOOL["name"]:
            return dict(block.input)

    text = "".join(b.text for b in message.content if b.type == "text").strip()
    if text:
        body = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, dict):
                return parsed

    raise RuntimeError(
        f"model did not return a {_ANALYSIS_TOOL['name']} tool call "
        f"(stop_reason={message.stop_reason!r})"
    )


def _normalise_payload(payload: dict) -> dict:
    """Undo a common malformed tool call before `_assemble` sees it.

    Some models don't emit the analysis as flat structured tool input.
    Two shapes seen in the wild, both of which leave `_assemble` with no
    `requirements` list and yield an empty analysis:

    - the whole object serialised to a JSON string in a single property,
      e.g. ``{"requirements": "{\\"summary\\": ..., \\"requirements\\": [...]}"}``;
    - the whole object nested one level down as a plain dict under a
      wrapper key, e.g. ``{"analysis": {"summary": ..., "requirements": [...]}}``.

    Recover it:
    - a value anywhere in `payload` that is the real payload — a dict, or a
      JSON string decoding to one, that looks the part (has a list
      `requirements` or a `summary`) → use that dict;
    - `payload["requirements"]` that is a JSON string decoding to a list →
      swap in the list.

    A well-formed payload (list `requirements`) is returned untouched.
    """
    if isinstance(payload.get("requirements"), list):
        return payload

    for value in payload.values():
        inner = value
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except (json.JSONDecodeError, ValueError):
                continue
        if isinstance(inner, dict) and (
            isinstance(inner.get("requirements"), list) or "summary" in inner
        ):
            return inner

    reqs = payload.get("requirements")
    if isinstance(reqs, str):
        try:
            decoded = json.loads(reqs)
        except (json.JSONDecodeError, ValueError):
            decoded = None
        if isinstance(decoded, list):
            return {**payload, "requirements": decoded}

    return payload


def _price(usage: anthropic.types.Usage) -> Cost:
    """Turn the API's token counts into a Cost, priced off the rate card."""
    read = getattr(usage, "cache_read_input_tokens", None) or 0
    write = getattr(usage, "cache_creation_input_tokens", None) or 0
    usd = (
        usage.input_tokens * _USD_PER_INPUT_TOKEN
        + usage.output_tokens * _USD_PER_OUTPUT_TOKEN
        + read * _USD_PER_CACHE_READ_TOKEN
        + write * _USD_PER_CACHE_WRITE_TOKEN
    )
    return Cost(
        usd=round(usd, 6),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_input_tokens=read,
        cache_write_input_tokens=write,
    )


def _assemble(payload: dict, cost: Cost, posting: str) -> Output:
    """Build the Output: coerce the model's payload to the contract.

    Every quote is checked against the posting and replaced with the
    closest real span (or ""); importance is forced onto the scale;
    requirements are sorted critical → high → medium → low and capped
    at COUNT_MAX.
    """
    requirements: list[Requirement] = []
    for item in payload.get("requirements") or []:
        if not isinstance(item, dict):
            continue
        point = str(item.get("point", "")).strip()
        if not point:
            continue
        importance = str(item.get("importance", "")).strip().lower()
        if importance not in _IMPORTANCE_RANK:
            importance = "medium"
        requirements.append(
            Requirement(
                point=point,
                quote=_closest_span(str(item.get("quote", "")), posting),
                importance=importance,
                rationale=str(item.get("rationale", "")).strip(),
            )
        )

    requirements.sort(key=lambda r: _IMPORTANCE_RANK[r.importance])
    requirements = requirements[:COUNT_MAX]

    reading = [
        str(line).strip()
        for line in (payload.get("reading_between_the_lines") or [])
        if str(line).strip()
    ][:RBTL_MAX]

    return Output(
        requirements=requirements,
        summary=str(payload.get("summary", "")).strip(),
        reading_between_the_lines=reading,
        cost=cost,
    )


def _closest_span(quote: str, posting: str) -> str:
    """Return the span of `posting` that `quote` refers to.

    The result is word-for-word the employer's text, in order, with
    internal whitespace collapsed to single spaces — so it is always a
    single line, safe to drop into a blockquote or a one-per-line
    format. It matches the posting under the same collapse (see
    `_matches_posting`); it is never a paraphrase.

    Exact match (modulo whitespace) wins immediately. Otherwise the
    quote is matched with flexible whitespace, trimming tokens from the
    ends until a real span is found — longest, earliest match preferred.
    No match (or a match shorter than `_MIN_FUZZY_TOKENS`) yields "".
    """
    tokens = quote.split()
    if not tokens:
        return ""

    for start in range(len(tokens)):
        for end in range(len(tokens), start, -1):
            if end - start < _MIN_FUZZY_TOKENS and not (start == 0 and end == len(tokens)):
                continue
            pattern = r"\s+".join(re.escape(tok) for tok in tokens[start:end])
            match = re.search(pattern, posting, flags=re.IGNORECASE)
            if match:
                return " ".join(match.group(0).split())
    return ""


def _matches_posting(quote: str, posting: str) -> bool:
    """True if `quote` is the posting's own words, in order (whitespace-insensitive)."""
    return bool(quote) and " ".join(quote.split()) in " ".join(posting.split())
