"""job-analyst — one discrete piece of functionality.

Given a job posting, analyse it from the hiring manager's perspective: a
prioritised, posting-anchored list of what the employer is really
weighing, a one–two sentence summary of what they're buying, and 3–6
signals the posting implies but doesn't state.

Public surface: run(), Input, Output, Requirement, Cost. Nothing else.
See docs/CONTRACT.md for what this module promises.
"""

from ._contract import Cost, Input, Output, Requirement
from ._core import run

__all__ = ["run", "Input", "Output", "Requirement", "Cost"]
