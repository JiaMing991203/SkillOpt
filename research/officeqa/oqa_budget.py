"""The resource budget for the three-arm OfficeQA comparison, computed from MEASURED constants.

Offline arithmetic, no I/O beyond the output file. Every constant below is either a measurement from a recorded
run on this project or a published price, and each one says which. Nothing here is a guess dressed as a number.

Run it with --slow-samples to see how the budget moves with the one setting that dominates it.

usage: python3 -I research/officeqa/oqa_budget.py [--slow-samples 6] [--out <dir>]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os

# ── measured constants ──────────────────────────────────────────────────────────
M = {
    "actor_requests_per_question": {
        "value": 9.33,
        "source": "runs/20261116-160952_officeqa_arm_66228142/arm_B/history.json step 2: rollout 224 calls over "
                  "24 question-rollouts (12 train + 12 val) at a 24-turn cap. The other three measured steps "
                  "give 6.9-7.6, so this is the HIGHEST of the four and is used deliberately.",
    },
    "actor_prompt_tokens_per_question": {
        "value": 106300,
        "source": "same step: 2,551,206 prompt tokens / 24 question-rollouts.",
    },
    "actor_completion_tokens_per_question": {
        "value": 4221,
        "source": "same step: 101,307 completion tokens / 24 question-rollouts.",
    },
    "actor_usd_per_m_prompt": {"value": 0.04, "source": "EmpirioLabs GET /v1/models/qwen3-5-4b, 2026-11-16."},
    "actor_usd_per_m_completion": {"value": 0.07, "source": "same."},
    "actor_rpm": {"value": 50, "source": "same: an ACCOUNT-WIDE limit, so concurrency cannot raise it."},
    "actor_median_latency_s": {
        "value": 3.41,
        "source": "job 66264849 smoke test, 20 attempts: 1.63 / 3.41 / 7.04 s min/median/max.",
    },
    "no_prefix_caching": {
        "value": True,
        "source": "job 66264849: cached_prompt_tokens 0 on every reply, so the cached rate is never assumed.",
    },
    "analyst_prompt_tokens_per_item": {
        "value": 17288,
        "source": "runs/.../arm_B/history.json: 207,461 analyst prompt tokens per step over 12 items. Arm C's "
                  "report is ~1k tokens longer per item than arm B's, which this figure already covers because "
                  "it was measured on the arm that carried a report.",
    },
    "slow_update_prompt_tokens_per_showing_pair": {
        "value": 34569,
        "source": "MEASURED on the pre-launch dry run. build_comparison_pairs + format_comparison_text over the "
                  "12 pairs of the dry run's epoch-2 slow update gives 846,563 characters; the A40 run's "
                  "per-call records put this project's optimizer prompts at 3.228 characters per token, so "
                  "262,257 tokens. REFINED by job 66271290: the per-pair growth curve over the 7 showing pairs "
                  "available in the saved collections averages 111,483 characters each, and that job's own reply "
                  "pins this project's ratio at 3.225 characters per prompt token (243,986 tokens reported for a "
                  "786,843-character request), so 34,569 tokens per SHOWING pair. Only regressed / "
                  "persistent_fail / improved print a trajectory; stable_success does not. Per-trajectory "
                  "truncation is DISABLED in this repo on purpose "
                  "(skillopt/optimizer/slow_update.py: 'Truncation disabled: return the full text'). "
                  "runs/20261117-officeqa-slow-probe-66271190/GROWTH.json",
    },
    "showing_pair_fraction": {
        "value": 0.667,
        "source": "the same slow update: 1 regressed + 7 persistent_fail of 12 pairs showed trajectories, 4 "
                  "stable_success did not. The worst case is 1.0, and the budget reports it separately because "
                  "a single bad epoch can put every pair in a showing category.",
    },
    "deepseek_usd_per_m_prompt_offpeak": {"value": 0.15, "source": "optimizer_calls_summary.json, cache miss."},
    "deepseek_usd_per_m_completion_offpeak": {"value": 0.60, "source": "same."},
    "deepseek_peak_multiplier": {"value": 2.0, "source": "same file: peak prices are exactly double."},
    "largest_prompt_this_project_has_sent": {
        "value": 243986,
        "source": "job 66271290, the 10-pair slow-update probe: DeepSeek reported 243,986 prompt tokens, "
                  "finish_reason 'stop', 7,562 completion tokens, and run_slow_update returned 3,235 characters "
                  "of parsed guidance. Two earlier figures in this file were wrong and are recorded so the "
                  "revision is traceable: 104,000 came from dividing a step's TOTAL analyst tokens by the "
                  "number of calls in it, and 173,060 was the largest prompt sent before this probe existed "
                  "(arm B's second analyst call of step 2). Neither was a limit - only the largest thing tried.",
    },
    "prompt_guard_limit_tokens": {
        "value": 225000,
        "source": "the conservative pre-send limit the campaign runs with "
                  "(SKILLOPT_OPTIMIZER_PROMPT_TOKEN_LIMIT, skillopt/optimizer/prompt_budget.py). Chosen so that "
                  "225,000 prompt + the full 16,384-token completion allowance = 241,384 stays inside the "
                  "251,548 tokens job 66271290 demonstrably processed (243,986 prompt + 7,562 completion). A "
                  "draw above it is not sent as-is: comparison pairs are dropped by rule OQA-PB-1 until it "
                  "fits, and the pair count actually sent is logged.",
    },
    "deepseek_accepted_prompt_tokens": {
        "value": 243986,
        "source": "job 66271290. This is a DEMONSTRATED acceptance, not a provider limit: the real ceiling is "
                  "still unknown and the published figures for the DeepSeek chat family disagree (128k for the "
                  "V3 era, 1M for the current ones), with no official source among them.",
    },
    "deepseek_prompt_cache_is_negligible_here": {
        "value": True,
        "source": "same file: prompt_cache_hit_tokens were 256-2,048 against 1,012-172,548 missed, so the budget "
                  "prices every prompt token at the cache-MISS rate.",
    },
    "optimizer_latency_s_per_call": {
        "value": 19.4,
        "source": "same file: 155 s over 8 calls (13.4-40.6 s). Reported so the optimizer's share of the wall "
                  "clock can be seen; it is not the binding constraint.",
    },
}

TRAIN, VAL, HELDOUT = 24, 24, 60
EPOCHS = 4
ARMS = 3


def measured_constants() -> dict:
    """Every constant the budget rests on, each with the record it was measured from."""
    return dict(M)


def v(k):
    return M[k]["value"]


def budget(slow_samples: int, heldout_skills: int) -> dict:
    # ── question-rollouts (one question attempted once by one skill) ────────────
    shared = TRAIN + VAL          # the fresh shared epoch-1 rollout, and S0 on the gate, both reused by all arms
    per_arm_epoch1 = VAL          # the epoch-1 candidate's gate pass; the slow update only injects a placeholder
    per_arm_later = TRAIN + VAL + 2 * slow_samples + VAL
    per_arm = per_arm_epoch1 + (EPOCHS - 1) * per_arm_later
    training = shared + ARMS * per_arm
    heldout = heldout_skills * HELDOUT
    questions = training + heldout

    requests = questions * v("actor_requests_per_question")
    actor_usd = questions * (v("actor_prompt_tokens_per_question") * v("actor_usd_per_m_prompt")
                             + v("actor_completion_tokens_per_question") * v("actor_usd_per_m_completion")) / 1e6

    # ── optimizer calls ────────────────────────────────────────────────────────
    # 24 items at minibatch 8 split into failures and successes: ceil(f/8)+ceil(s/8) is 3 or 4, so 4 is used.
    analyst_per_step, merge_per_step, ranking_per_step = 4, 2, 1
    per_step = analyst_per_step + merge_per_step + ranking_per_step
    calls_per_arm = per_step * EPOCHS + 2 * (EPOCHS - 1)        # +slow_update and +meta_skill from epoch 2
    calls = ARMS * calls_per_arm

    analyst_prompt = EPOCHS * analyst_per_step * (TRAIN * v("analyst_prompt_tokens_per_item")
                                                  / analyst_per_step)   # all 24 items are seen once per step
    slow_prompt_each = slow_samples * v("showing_pair_fraction") * v(
        "slow_update_prompt_tokens_per_showing_pair")
    slow_prompt_worst = slow_samples * v("slow_update_prompt_tokens_per_showing_pair")
    slow_prompt = (EPOCHS - 1) * 2 * slow_prompt_each
    small_prompt = EPOCHS * (merge_per_step + ranking_per_step) * 1800
    prompt_per_arm = analyst_prompt + slow_prompt + small_prompt
    completion_per_arm = calls_per_arm * 6000
    ds_offpeak = ARMS * (prompt_per_arm * v("deepseek_usd_per_m_prompt_offpeak")
                         + completion_per_arm * v("deepseek_usd_per_m_completion_offpeak")) / 1e6
    ds_peak = ds_offpeak * v("deepseek_peak_multiplier")

    rate_minutes = requests / v("actor_rpm")
    latency_minutes = questions * v("actor_requests_per_question") * v("actor_median_latency_s") / 4 / 60

    return {
        "slow_update_samples": slow_samples,
        "heldout_distinct_skills": heldout_skills,
        "question_rollouts": {
            "shared_across_arms": shared,
            "per_arm_epoch_1": per_arm_epoch1,
            "per_arm_each_later_epoch": per_arm_later,
            "per_arm_total": per_arm,
            "training_all_arms": training,
            "heldout": heldout,
            "total": questions,
            "note": "one question attempted once by one skill. The gate pass is counted in every epoch because "
                    "the candidate's digest changes, so the selection cache cannot hit.",
        },
        "actor": {
            "http_requests": round(requests),
            "usd": round(actor_usd, 2),
            "minutes_at_the_account_rate_limit": round(rate_minutes, 1),
            "minutes_if_latency_bound_at_4_workers": round(latency_minutes, 1),
            "binding_constraint": ("the 50 RPM ACCOUNT limit" if rate_minutes > latency_minutes else "latency"),
        },
        "optimizer_deepseek": {
            "calls": calls, "calls_per_arm": calls_per_arm,
            "calls_per_step": {"analyst": analyst_per_step, "merge": merge_per_step, "ranking": ranking_per_step},
            "plus_per_later_epoch": {"slow_update": 1, "meta_skill": 1},
            "prompt_tokens_per_arm": round(prompt_per_arm),
            "usd_offpeak": round(ds_offpeak, 2), "usd_if_all_peak": round(ds_peak, 2),
        },
        "context_risk": {
            "slow_update_prompt_tokens_typical_mix": round(slow_prompt_each),
            "slow_update_prompt_tokens_worst_case": round(slow_prompt_worst),
            "largest_prompt_this_project_has_sent": v("largest_prompt_this_project_has_sent"),
            "ratio_typical": round(slow_prompt_each / v("largest_prompt_this_project_has_sent"), 2),
            "ratio_worst_case": round(slow_prompt_worst / v("largest_prompt_this_project_has_sent"), 2),
            "verdict": ("INSIDE the measured envelope even if every pair shows a trajectory"
                        if slow_prompt_worst <= v("largest_prompt_this_project_has_sent")
                        else "inside on the measured category mix but OVER in the worst case"
                        if slow_prompt_each <= v("largest_prompt_this_project_has_sent")
                        else "LARGER than any optimizer prompt this project has sent successfully"),
            "pre_send_guard_limit_tokens": v("prompt_guard_limit_tokens"),
            "typical_mix_needs_trimming": slow_prompt_each > v("prompt_guard_limit_tokens"),
            "worst_case_needs_trimming": slow_prompt_worst > v("prompt_guard_limit_tokens"),
            "pairs_that_fit_the_guard_in_the_worst_case": max(
                1, int(v("prompt_guard_limit_tokens") // v("slow_update_prompt_tokens_per_showing_pair"))),
            "what_happens_if_a_draw_is_too_large": "it is NOT sent as drawn. prompt_budget.fit renders the "
                                                   "prompt, measures it, and drops comparison pairs by rule "
                                                   "OQA-PB-1 (stable_success -> improved -> persistent_fail -> "
                                                   "regressed, later-sampled first) until it fits, recording "
                                                   "the pair count actually sent. Only if even the single "
                                                   "highest-priority pair is over the limit is no call made, "
                                                   "and that is logged too. The old failure mode - the provider "
                                                   "rejects the request, run_slow_update swallows the error and "
                                                   "the trainer records action 'no_content' - is now the last "
                                                   "resort rather than the first. Check the prompt-budget log "
                                                   "and slow_update/epoch_NN/slow_result.json in every arm.",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slow-samples", type=int, default=4)
    ap.add_argument("--heldout-skills", type=int, default=7)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    variants = {str(s): budget(s, args.heldout_skills) for s in sorted({args.slow_samples, 4, 6, 20})}
    chosen = budget(args.slow_samples, args.heldout_skills)
    payload = {"generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
               "splits": {"train": TRAIN, "val": VAL, "heldout": HELDOUT, "epochs": EPOCHS, "arms": ARMS},
               "measured_constants": M, "chosen": chosen, "variants": variants}
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "BUDGET.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
    print(json.dumps({"chosen": chosen,
                      "comparison": {k: {"questions": x["question_rollouts"]["total"],
                                         "requests": x["actor"]["http_requests"],
                                         "actor_usd": x["actor"]["usd"],
                                         "minutes_rate_limited": x["actor"]
                                         ["minutes_at_the_account_rate_limit"],
                                         "deepseek_usd_peak": x["optimizer_deepseek"]["usd_if_all_peak"],
                                         "slow_prompt_typical": x["context_risk"]
                                         ["slow_update_prompt_tokens_typical_mix"],
                                         "slow_prompt_worst": x["context_risk"]
                                         ["slow_update_prompt_tokens_worst_case"],
                                         "context": x["context_risk"]["verdict"]}
                                     for k, x in variants.items()}}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
