"""Opt-in reasoning extraction for ALFWorld responses without <think> tags.

Upstream SkillOpt (``rollout._extract_think``) stores a step's ``reasoning``
only when the response contains a complete ``<think>...</think>`` pair. Qwen
in non-thinking mode often writes its explanation as plain text before
``<action>`` (no tags, or only a closing ``</think>``), so ``reasoning`` is
empty and the reflection prompt carries no reasoning for that step.

``reasoning_parser="qwen_compat"`` recovers that visible explanation:

1. If the strict parser finds non-empty reasoning (first complete
   ``<think>...</think>`` pair anywhere in the response), that exact text is
   returned (``source="think_tags"``). Compat mode therefore never changes
   reasoning that upstream already extracts.
2. Otherwise only the text BEFORE the first ``<action>`` is considered; the
   action itself and anything after it are never treated as reasoning (the
   same ``<action>`` anchor the upstream action parser uses).
3. Every ``<think>``/``</think>`` delimiter is removed from that prefix. If
   text remains, it is returned whitespace-normalized to a single line
   (``source="untagged_prefix"``).
4. If nothing remains, the result is ``None`` (``source="none"``). No text is
   ever invented.

Recovered text is model output, i.e. untrusted trajectory data. It is placed
in the same ``reasoning`` field the strict parser fills, with no new prompt
channel. Collapsing it to one line keeps it from forging the reflection
formatter's line structure (``[step N action]`` / ``### Trajectory``). The raw
response is always preserved unchanged in ``model_response``.
"""
from __future__ import annotations

import re

REASONING_PARSERS = ("strict", "qwen_compat")

_THINK_PAIR = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_THINK_DELIM = re.compile(r"</?think>")
_WHITESPACE = re.compile(r"\s+")


def extract_reasoning_strict(model_response: str) -> str | None:
    """Upstream behavior (identical to ``rollout._extract_think``)."""
    match = _THINK_PAIR.search(model_response)
    return match.group(1).strip() if match else None


def extract_reasoning_compat(model_response: str) -> tuple[str | None, str]:
    """Return ``(reasoning, source)``; see the module docstring for the rules."""
    if not isinstance(model_response, str) or not model_response.strip():
        return None, "none"
    strict = extract_reasoning_strict(model_response)
    if strict:
        return strict, "think_tags"
    head = model_response.split("<action>", 1)[0]
    untagged = _WHITESPACE.sub(" ", _THINK_DELIM.sub(" ", head)).strip()
    if untagged:
        return untagged, "untagged_prefix"
    return None, "none"


def extract_reasoning(model_response: str, parser: str = "strict") -> tuple[str | None, str | None]:
    """Dispatch on the configured parser.

    Returns ``(reasoning, source)``. ``source`` is ``None`` for the strict
    parser, so strict-mode trajectories keep exactly the upstream fields.
    """
    if parser == "strict":
        return extract_reasoning_strict(model_response), None
    if parser == "qwen_compat":
        return extract_reasoning_compat(model_response)
    raise ValueError(f"unknown reasoning_parser {parser!r}; expected one of {REASONING_PARSERS}")
