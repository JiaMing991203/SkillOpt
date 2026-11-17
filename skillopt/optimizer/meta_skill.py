"""Optimizer-side meta skill memory for cross-epoch optimization guidance.

This module maintains a compact optimizer-facing memory distilled from
adjacent-epoch skill comparisons. Unlike ``slow_update``, it does not
modify the target skill document. Instead, it produces guidance meant to
improve future optimizer behavior when proposing, merging, and ranking edits.
"""
from __future__ import annotations

import traceback

from skillopt.model import chat_optimizer
from skillopt.optimizer import prompt_budget
from skillopt.optimizer.slow_update import format_comparison_text
from skillopt.prompts import load_prompt
from skillopt.utils import extract_json


def format_meta_skill_context(meta_skill_content: str) -> str:
    """Render optimizer memory into a prompt-ready context block."""
    content = (meta_skill_content or "").strip()
    if not content:
        return ""
    return (
        "## Optimizer Meta Skill\n"
        "This is optimizer-side memory distilled from prior epoch transitions in "
        "this environment. Use it to improve how you propose, merge, and rank "
        "skill edits. Prefer it when the current evidence is ambiguous, but do "
        "not force it if the current trajectories clearly contradict it.\n\n"
        f"{content}"
    )


def run_meta_skill(
    prev_skill: str,
    curr_skill: str,
    comparison_pairs: list[dict],
    *,
    prev_meta_skill_content: str = "",
    system_prompt: str | None = None,
) -> dict | None:
    """Produce updated optimizer-side meta skill from adjacent epochs."""
    actual_system = system_prompt if system_prompt is not None else load_prompt("meta_skill")

    prev_meta_section = (
        prev_meta_skill_content.strip()
        if prev_meta_skill_content and prev_meta_skill_content.strip()
        else "(No previous optimizer meta skill — this is the first update.)"
    )

    def _build_user(selected: list[dict]) -> str:
        return (
            f"## Previous Epoch Last-Step Skill\n{prev_skill}\n\n"
            f"## Current Epoch Last-Step Skill\n{curr_skill}\n\n"
            f"## Previous Optimizer Meta Skill\n"
            f"The following optimizer memory was available during the current epoch. "
            f"Reflect on whether it improved or harmed the quality of edits.\n\n"
            f"{prev_meta_section}\n\n"
            f"## Longitudinal Comparison (same tasks, two last-step skills)\n"
            f"{format_comparison_text(selected)}"
        )

    # The same pre-send size check and the same drop rule as the slow update. The meta skill reuses the slow
    # update's comparison pairs, so its prompt is nearly the same size, and it must obey the same limit.
    user, _sent_pairs, budget_record = prompt_budget.fit(
        actual_system, list(comparison_pairs), _build_user, stage="meta_skill",
    )
    if budget_record.get("skip"):
        print(f"    [meta skill] NOT SENT: {budget_record.get('skip_reason', 'over the prompt size limit')}")
        prompt_budget.log(budget_record, guidance_produced=False, note="no call was made")
        return None
    if budget_record.get("checked"):
        print(f"    [meta skill] prompt check: {budget_record['pairs_sent']}/"
              f"{budget_record['pairs_offered']} pairs, ~{budget_record.get('estimated_tokens_sent')} tokens "
              f"against a {budget_record['limit_tokens']}-token limit")

    try:
        response, _ = chat_optimizer(
            system=actual_system,
            user=user,
            max_completion_tokens=16384,
            retries=3,
            stage="meta_skill",
        )
        result = extract_json(response)
        if result and result.get("meta_skill_content"):
            prompt_budget.log(budget_record, guidance_produced=True)
            return {
                "reasoning": str(result.get("reasoning", "")).strip(),
                "meta_skill_content": str(result["meta_skill_content"]).strip(),
            }
        prompt_budget.log(budget_record, guidance_produced=False,
                          note="the optimizer replied but the reply carried no meta_skill_content")
        return None
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        prompt_budget.log(budget_record, guidance_produced=False, note=f"{type(exc).__name__}: {str(exc)[:400]}")

    return None
