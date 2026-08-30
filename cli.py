"""Run this capability in isolation, straight from a terminal.

    uv run python cli.py --input examples/sample.txt
    pbpaste | uv run python cli.py

The point of this file: prove the module is useful on its own, with no
other module present. The analysis prints as Markdown to stdout; the
run's estimated cost goes to stderr.
"""

from __future__ import annotations

import argparse
import sys

try:  # load ANTHROPIC_API_KEY from a local .env when running in isolation
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:
    pass

from job_analyst import Input, Output, run


def _render(output: Output) -> str:
    lines = [
        "# Job posting analysis",
        "",
        "## What this employer is really buying",
        "",
        output.summary or "_(no summary)_",
        "",
        "## What they're weighing (most important first)",
        "",
    ]
    for i, req in enumerate(output.requirements, start=1):
        lines.append(f"{i}. **[{req.importance}]** {req.point}")
        if req.quote:
            lines.append(f"   > {req.quote}")
        if req.rationale:
            lines.append(f"   — {req.rationale}")
        lines.append("")
    if output.reading_between_the_lines:
        lines += ["## Reading between the lines", ""]
        lines += [f"- {item}" for item in output.reading_between_the_lines]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--input",
        type=argparse.FileType("r"),
        default=sys.stdin,
        help="job posting file (default: read stdin)",
    )
    parser.add_argument(
        "--role-hint",
        default=None,
        metavar="TEXT",
        help="title / company, if it isn't obvious in the posting",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=12,
        help="target number of requirements (clamped to 6..18)",
    )
    parser.add_argument(
        "--guidance",
        default=None,
        metavar="TEXT",
        help="operator steering for the analysis",
    )
    args = parser.parse_args()

    data = Input(
        posting=args.input.read(),
        role_hint=args.role_hint,
        count=args.count,
        expert_guidance=args.guidance,
    )
    output = run(data)

    sys.stdout.write(_render(output))

    c = output.cost
    sys.stderr.write(
        f"---\nest. ${c.usd:.4f} — {c.input_tokens} input, {c.output_tokens} output, "
        f"{c.cache_read_input_tokens} cache-read, {c.cache_write_input_tokens} cache-write tokens\n"
    )


if __name__ == "__main__":
    main()
