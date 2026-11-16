"""Both arms must reflect on the SAME epoch-1 trajectories, which is done by injecting one rollout into both.

The comparison rests on this: `STAGE=arm` copies the shared rollout into the trainer's
`steps/step_0001/rollout/`, and `run_batch` is expected to consume it and generate nothing. If it instead
re-rolled any item, that arm would differ from the other by the actor's sampling (temperature 0.7) as well as by
the stage report, and with 12 questions that noise would swamp the effect being measured.

    python3.12 -m pytest tests/test_officeqa_shared_rollout_injection.py -q
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

pytest.importorskip("skillopt.envs.officeqa.rollout", reason="needs SkillOpt importable (Python >= 3.10)")

from skillopt.envs.officeqa import rollout as RO  # noqa: E402

IDS = [f"UID{i:04d}" for i in (33, 34, 63, 17, 30, 31, 14, 26, 79, 28, 65, 85)]


def _write_shared_rollout(root, ids):
    """A rollout directory shaped the way the adapter writes one."""
    os.makedirs(os.path.join(root, "predictions"), exist_ok=True)
    rows = []
    for n, item_id in enumerate(ids):
        pred = os.path.join(root, "predictions", item_id)
        os.makedirs(pred, exist_ok=True)
        with open(os.path.join(pred, "conversation.json"), "w") as f:
            json.dump([{"role": "user", "content": "## Question\nq?"},
                       {"type": "message", "content": "<answer>x</answer>"}], f)
        for name in ("target_system_prompt.txt", "target_user_prompt.txt"):
            with open(os.path.join(pred, name), "w") as f:
                f.write("recorded")
        rows.append({"id": item_id, "question": "q?", "predicted_answer": "x",
                     "hard": 1 if n == 0 else 0, "soft": 1.0 if n == 0 else 0.0, "n_turns": 2})
    with open(os.path.join(root, "results.jsonl"), "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return rows


@pytest.fixture()
def no_target_calls(monkeypatch):
    """Any attempt to call the actor fails the test loudly instead of silently costing a rollout."""
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("run_batch called the target although every item was already recorded")

    monkeypatch.setattr(RO, "chat_target_messages", _forbidden)
    return _forbidden


def test_a_fully_injected_rollout_generates_nothing(tmp_path, no_target_calls):
    root = tmp_path / "rollout"
    rows = _write_shared_rollout(str(root), IDS)
    items = [{"id": i, "question": "q?", "source_files": "f.txt", "source_docs": "u?page=1"} for i in IDS]

    results = RO.run_batch(items, str(root), "skill", workers=4, max_tool_turns=24)

    assert len(results) == len(IDS)
    assert [str(r["id"]) for r in results] == [str(r["id"]) for r in rows]
    assert sum(1 for r in results if r.get("hard")) == 1, "the injected scores are returned unchanged"


def test_the_injected_order_does_not_have_to_match_the_batch_order(tmp_path, no_target_calls):
    """The trainer shuffles the batch by seed; resume is by id, so a different order must still be a full hit."""
    root = tmp_path / "rollout"
    _write_shared_rollout(str(root), IDS)
    shuffled = list(reversed(IDS))
    items = [{"id": i, "question": "q?", "source_files": "f.txt", "source_docs": "u?page=1"} for i in shuffled]
    assert len(RO.run_batch(items, str(root), "skill", workers=4, max_tool_turns=24)) == len(IDS)


def test_one_missing_item_would_be_rolled(tmp_path, monkeypatch, capsys):
    """The guard is not vacuous: an incomplete injection does reach the actor, which is what must never happen."""
    docs = tmp_path / "docs" / "transformed"
    docs.mkdir(parents=True)
    (docs / "f.txt").write_text("a line with 646,379 in it\n", encoding="utf-8")
    monkeypatch.setenv("OFFICEQA_DOCS_DIR", str(tmp_path / "docs"))

    root = tmp_path / "rollout"
    _write_shared_rollout(str(root), IDS[:-1])  # 11 of 12 recorded
    reached = []

    def _record(*_args, **_kwargs):
        reached.append(1)
        raise RuntimeError("stop here; the point is that the actor was reached")

    monkeypatch.setattr(RO, "chat_target_messages", _record)
    items = [{"id": i, "question": "q?", "source_files": "f.txt", "source_docs": "u?page=1"} for i in IDS]
    RO.run_batch(items, str(root), "skill", workers=1, max_tool_turns=24,
                 data_dirs=[str(tmp_path / "docs")])
    assert "resuming: 11/12 already done" in capsys.readouterr().out
    assert reached, "a missing item must be rolled, so a partial injection cannot pass unnoticed"


def test_the_injected_results_are_what_the_analyst_would_read(tmp_path, no_target_calls):
    """The injected traces, not fresh ones, are what reaches fmt_minibatch_trajectories."""
    from skillopt.gradient.reflect import fmt_minibatch_trajectories

    root = tmp_path / "rollout"
    rows = _write_shared_rollout(str(root), IDS[:3])
    items = [{"id": i, "question": "q?", "source_files": "f.txt", "source_docs": "u?page=1"} for i in IDS[:3]]
    results = RO.run_batch(items, str(root), "skill", workers=2, max_tool_turns=24)
    failures = [r for r in results if not r.get("hard")]
    os.environ.pop("OFFICEQA_STAGE_REPORT_DIR", None)
    prompt = fmt_minibatch_trajectories(failures, os.path.join(str(root), "predictions"))
    assert "recorded" in prompt and "<answer>x</answer>" in prompt
    for row in failures:
        assert f"id={row['id']}" in prompt
    assert len(rows) == 3


# ── the corpus override must actually point at the corpus ──────────────────────

def test_the_data_dirs_override_form_the_job_script_uses_resolves(tmp_path, monkeypatch):
    """--cfg-options has no list syntax, so `env.data_dirs=[/path]` is a string that resolves to nothing.

    The job script passed that bracket form until 2026-11-16, which meant the corpus came from the exported
    OFFICEQA_DOCS_DIR alone and a wrong value there would have fallen through to a built-in candidate
    (data/officeqa_docs_official, ~/officeqa-sparse/...) instead of failing.
    """
    from skillopt.config import apply_overrides
    from skillopt.envs.officeqa.tool_runtime import resolve_docs_roots

    docs = tmp_path / "corpus"
    (docs / "transformed").mkdir(parents=True)
    (docs / "jsons").mkdir()
    monkeypatch.delenv("OFFICEQA_DOCS_DIR", raising=False)

    cfg = {"env": {"data_dirs": []}}
    apply_overrides(cfg, [f"env.data_dirs={docs}"])
    assert resolve_docs_roots(cfg["env"]["data_dirs"]) == [str((docs / "transformed").resolve())]

    bracketed = {"env": {"data_dirs": []}}
    apply_overrides(bracketed, [f"env.data_dirs=[{docs}]"])
    assert bracketed["env"]["data_dirs"] == f"[{docs}]", "the value is cast to a string, not a list"
    with pytest.raises(FileNotFoundError):
        resolve_docs_roots(bracketed["env"]["data_dirs"])


def test_the_job_script_passes_the_resolving_form():
    """Pin it in the script itself, so the bracket form cannot come back unnoticed."""
    script = open(os.path.join(REPO, "research", "cluster", "officeqa.sbatch"), encoding="utf-8").read()
    code = "\n".join(l for l in script.splitlines() if not l.lstrip().startswith("#"))
    assert '"env.data_dirs=$OFFICEQA_DOCS_DIR"' in code
    assert "env.data_dirs=[$OFFICEQA_DOCS_DIR]" not in code
