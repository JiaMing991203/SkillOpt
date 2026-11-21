"""Amendment A-1: the observability OQA-FOR-1 found missing, and the one field whose meaning changed.

Every test here is named after something the 2026-11-17 campaign actually did. The three real
counterexamples the audit produced are used directly:

  * a reply with finish_reason "stop", content null and 1,502 completion tokens (arm B epoch 2, UID0170)
    -- 331 of 528 selection observations ended that way and the row recorded none of it;
  * a reply with finish_reason "length" at the 8,000-token cap (arm A epoch 2, UID0161) -- recorded as
    "Model neither produced a tool request nor a final answer", the same string as the empty reply;
  * arm B's one violated forecast (epoch 3, UID0019) against a report whose `n_distinct_diagnoses` was
    the literal 0.

The last test regenerates two REAL historical reports and requires their bytes to be unchanged. It needs
the campaign directory and skips without it.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from skillopt.model import common as COMMON  # noqa: E402
from skillopt.model import spend_ledger as LEDGER  # noqa: E402

CAMPAIGN = os.environ.get("OQA_CAMPAIGN_ROOT", "")


def _research(name: str):
    d = os.path.join(REPO, "research", "officeqa")
    if d not in sys.path:
        sys.path.insert(0, d)
    path = os.path.join(d, f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"{name}_fu_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── stubs shaped like the provider's real objects ─────────────────────────────

class _Fn:
    def __init__(self, name, arguments="{}"):
        self.name, self.arguments = name, arguments


class _TC:
    def __init__(self, name, tid="call_1", arguments="{}"):
        self.id, self.type, self.function = tid, "function", _Fn(name, arguments)

    def model_dump(self, mode="json"):
        return {"id": self.id, "type": self.type,
                "function": {"name": self.function.name, "arguments": self.function.arguments}}


class _Usage:
    def __init__(self, **kw):
        self._d = kw
        for k, v in kw.items():
            setattr(self, k, v)

    def model_dump(self):
        return dict(self._d)


class _Msg:
    def __init__(self, content=None, tool_calls=None, reasoning_content=None, extra=None):
        self.content, self.tool_calls = content, tool_calls or []
        self.reasoning_content = reasoning_content
        self.model_extra = dict(extra or {})

    def model_dump(self):
        d = {"role": "assistant", "content": self.content,
             "reasoning_content": self.reasoning_content}
        d.update(self.model_extra)
        return d


class _Choice:
    def __init__(self, message, finish_reason):
        self.message, self.finish_reason = message, finish_reason


class _Resp:
    def __init__(self, message, finish_reason, usage=None, rid="chatcmpl-x", model="qwen3-5-4b"):
        self.choices = [_Choice(message, finish_reason)]
        self.usage, self.id, self.model = usage, rid, model
        self.system_fingerprint = None


# ── the adapter ───────────────────────────────────────────────────────────────

def test_the_adapter_is_unchanged_when_no_response_is_handed_to_it():
    """Every other backend calls it with the message alone, and must see exactly what it saw before."""
    m = _Msg(content="hello", tool_calls=[_TC("read")])
    out = COMMON.compat_message_from_chat_message(m)
    assert out.content == "hello"
    assert [tc.function.name for tc in out.tool_calls] == ["read"]
    assert out.metadata == {}, "no response, no metadata - this is the compatibility guarantee"


def test_the_adapter_carries_the_finish_reason_and_the_usage_the_campaign_lost():
    """The real arm B epoch-2 UID0170 reply: finish_reason stop, content null, 1,502 completion tokens.

    The campaign's row recorded `last_finish_reason: ""` for this and for every other rollout, because
    the adapter dropped the finish reason before the OfficeQA loop could see it.
    """
    usage = _Usage(prompt_tokens=4977, completion_tokens=1502, total_tokens=6479,
                   cost_usd=0.00034775, cached_tokens=3168)
    m = _Msg(content=None)
    out = COMMON.compat_message_from_chat_message(m, _Resp(m, "stop", usage), _Choice(m, "stop"))
    md = out.metadata
    assert md["finish_reason"] == "stop"
    assert md["completion_tokens"] == 1502
    assert md["content_is_null"] is True and md["content_present"] is False
    assert md["reasoning_content_present"] is False
    assert md["provider_cost_usd"] == 0.00034775
    assert md["usage"]["prompt_tokens"] == 4977
    assert md["n_tool_calls"] == 0 and md["tool_names"] == []
    assert md["metadata_source"] == "live"
    # and the adapter's own contract still holds: content is a string, never None
    assert out.content == ""


def test_a_field_the_adapter_does_not_model_is_carried_through_instead_of_dropped():
    """OQA-FOR-1 could not rule out that the answer arrived in a field nobody read. Now it can.

    `reasoning_content` was null in all 7,909 logged attempts, which refutes that hypothesis for THIS
    provider. It does not refute it for the next one, so an unmodelled field is now visible.
    """
    m = _Msg(content=None, extra={"reasoning": "a long private thought", "answer_text": "42",
                                  "chunk_ids": [1, 2, 3], "nothing": None})
    out = COMMON.compat_message_from_chat_message(m, _Resp(m, "stop"), _Choice(m, "stop"))
    got = out.metadata["unmodelled_message_fields"]
    assert got["reasoning"]["type"] == "str"
    assert got["reasoning"]["chars"] == len("a long private thought")
    assert got["answer_text"]["prefix"] == "42"
    assert got["chunk_ids"]["type"] == "list" and got["chunk_ids"]["len"] == 3
    assert "nothing" not in got, "a null field is not a surprise"
    assert "content" not in got and "tool_calls" not in got, "modelled fields are not duplicated here"


def test_the_tool_calls_the_reply_asked_for_are_recorded():
    """The call log's response record had no tool_calls field, so the raw side could not say which tool
    a reply requested -- including the 211 calls to an `answer` tool that does not exist."""
    m = _Msg(content=None, tool_calls=[_TC("grep", "c1"), _TC("answer", "c2")])
    out = COMMON.compat_message_from_chat_message(m, _Resp(m, "tool_calls"), _Choice(m, "tool_calls"))
    assert out.metadata["n_tool_calls"] == 2
    assert out.metadata["tool_names"] == ["grep", "answer"]


# ── the loop's named exits ────────────────────────────────────────────────────

def test_each_of_the_campaigns_four_real_reply_shapes_gets_its_own_cause():
    """One fail_reason covered three different provider behaviours. These are the shapes that occurred."""
    RO = _research("oqa_forensics")  # only to prove the taxonomies agree; the function under test:
    from skillopt.envs.officeqa.rollout import _no_answer_cause
    empty = _no_answer_cause("", {"finish_reason": "stop", "content_is_null": True,
                                  "completion_tokens": 1502}, "")
    assert empty == "empty_reply_content_null"
    blank = _no_answer_cause("   ", {"finish_reason": "stop", "content_is_null": False}, "")
    assert blank == "empty_reply_content_blank"
    cut = _no_answer_cause("Looking at the table data more carefully, I need to extrac",
                           {"finish_reason": "length", "content_is_null": False}, "")
    assert cut == "completion_truncated_at_cap"
    prose = _no_answer_cause("I think it is about 12 but I am not sure.",
                             {"finish_reason": "stop", "content_is_null": False}, "")
    assert prose == "prose_without_answer_tag"
    at_cap = _no_answer_cause("", {"finish_reason": "stop", "content_is_null": True},
                              "final_turn_without_answer")
    assert at_cap == "empty_reply_content_null+final_turn_without_answer"
    assert RO.RULE == "OQA-FOR-1"


def test_the_loops_control_flow_is_unchanged_by_the_naming():
    """The cause is recorded; which episodes count as failures is not touched.

    `_no_answer_cause` is a pure function of the reply, called only where a fail_reason was already
    being set. This asserts the property that matters: it never returns something that could be mistaken
    for success, and it does not look at the fail_reason at all.
    """
    from skillopt.envs.officeqa.rollout import _no_answer_cause
    for resp, md in (("", {}), ("x", {}), ("", {"finish_reason": None})):
        got = _no_answer_cause(resp, md, "")
        assert got and "answer_emitted" not in got


# ── the ledger records the provider's figure without using it ─────────────────

def test_the_providers_own_cost_is_recorded_and_the_charge_is_still_ours(tmp_path, monkeypatch):
    """Over the 16 logged campaign jobs the provider charged $3.2768 where the ledger charged $5.3159,
    because the provider does not bill cache reads. Recording both is what let that be seen. The cap
    must still be enforced on OUR figure, so the guard cannot be weakened by a provider's number."""
    d = str(tmp_path)
    for k, v in (("SKILLOPT_SPEND_LEDGER_DIR", d),
                 ("SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET", "100"),
                 ("SKILLOPT_LEDGER_MAX_USD_TARGET", "5.0"),
                 ("SKILLOPT_LEDGER_BLOCK_ATTEMPTS_TARGET", "4"),
                 ("SKILLOPT_LEDGER_BLOCK_USD_TARGET", "0.05"),
                 ("SKILLOPT_LEDGER_RESERVE_USD_TARGET", "0.02"),
                 ("SKILLOPT_LEDGER_PRICE_TARGET", "0.04,0.07,0.02")):
        monkeypatch.setenv(k, v)
    LEDGER._state.clear()
    LEDGER._owner = "fu1r0p1"
    LEDGER.reserve("target", "rollout")
    charged = LEDGER.record_usage("target", "rollout", 10_000, 1_000,
                                  cached_tokens=5_000, provider_cost_usd=0.0001)
    LEDGER._state.clear()
    rows = [json.loads(l) for l in open(os.path.join(d, "spend", "fu1r0p1.jsonl"),
                                        encoding="utf-8") if l.strip()]
    usage = [r for r in rows if r.get("kind") == "usage"][0]
    assert usage["provider_cost_usd"] == 0.0001
    assert abs(usage["usd"] - charged) < 1e-12
    assert usage["usd"] > 0.0001, "our figure is the larger one here, and it is the one charged"
    # and omitting it leaves the record exactly as it was
    LEDGER._owner = "fu1r0p2"
    LEDGER.reserve("target", "rollout")
    LEDGER.record_usage("target", "rollout", 10_000, 1_000, cached_tokens=5_000)
    LEDGER._state.clear()
    rows2 = [json.loads(l) for l in open(os.path.join(d, "spend", "fu1r0p2.jsonl"),
                                         encoding="utf-8") if l.strip()]
    usage2 = [r for r in rows2 if r.get("kind") == "usage"][0]
    assert "provider_cost_usd" not in usage2


# ── the diagnosis counter ─────────────────────────────────────────────────────

def test_a_confirmed_forecast_is_not_counted_as_a_diagnosis():
    """Arm B's 96 reports held 561 confirmed forecasts, 1 violated and 2,114 unsettled, and the field
    said 0 diagnoses because of an `else 0`. The four kinds are now separate, and only what the ACTOR
    did or failed to do is actionable."""
    R = _research("oqa_reports")
    _, IR, CHAIN, REPORT, REPLAY, OB = R._lc_modules()

    class _FakeReport:
        @staticmethod
        def _forecast_rows(replay):
            return replay["rows"]

        @staticmethod
        def group(ledger):
            return ledger

    action = {"rows": [{"class": REPLAY.FORECAST_CONFIRMED}, {"class": REPLAY.FORECAST_CONFIRMED},
                       {"class": REPLAY.FORECAST_VIOLATED},
                       {"class": REPLAY.FORECAST_UNRESOLVED}]}
    counts = R.diagnosis_counts(action, {"ledger": []}, R.LC_ACTION, _FakeReport, REPLAY, CHAIN)
    assert counts == {"confirmations": 2, "model_refutations": 1, "execution_diagnoses": 0,
                      "unknowns": 1, "actionable": 0, "note": counts["note"]}
    assert counts["execution_diagnoses"] == 0, (
        "an Action-Only report still reports 0 - but now because the vocabulary cannot express an "
        "execution diagnosis, not because of a literal")

    stage = {"rows": [{"class": REPLAY.FORECAST_CONFIRMED}]}
    ledger = [{"state": CHAIN.VIOLATED}, {"state": CHAIN.OUTSTANDING}, {"state": CHAIN.UNKNOWN},
              {"state": CHAIN.DISCHARGED}]
    counts2 = R.diagnosis_counts(stage, {"ledger": ledger}, "LC_STAGE", _FakeReport, REPLAY, CHAIN)
    assert counts2["execution_diagnoses"] == 2, "VIOLATED and OUTSTANDING are about the actor"
    assert counts2["unknowns"] == 1 and counts2["confirmations"] == 2
    assert counts2["model_refutations"] == 0


# ── provenance ────────────────────────────────────────────────────────────────

def test_the_trace_digest_has_one_definition():
    """It was copied in two places. A provenance check that uses a copy is a check on the copy."""
    R = _research("oqa_reports")
    T = _research("oqa_trace")
    assert R.trace_digest.__doc__ and "oqa_trace" in R.trace_digest.__doc__
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        d = os.path.join(root, "UID0001")
        os.makedirs(d)
        with open(os.path.join(d, "conversation.json"), "w", encoding="utf-8") as f:
            f.write('[{"role": "user", "content": "q"}]')
        with open(os.path.join(d, "target_user_prompt.txt"), "w", encoding="utf-8") as f:
            f.write("## Question\nq\n")
        assert R.trace_digest(root, "UID0001") == T.trace_digest(root, "UID0001")


def test_an_attempt_is_identified_by_more_than_its_question_id_or_its_trace():
    """Both of the audit's real failure modes, in one test.

    A question id recurs in all four epochs. A trace digest can collide: arm C's epoch-3 UID0019 record
    is byte-identical to the shared epoch-1 record, because both hold three EMPTY assistant messages and
    two identical tool calls. The row digest is what separates them, and when the row carries no reply
    metadata that is reported as unavailable rather than guessed.
    """
    R = _research("oqa_reports")
    import tempfile
    conv = ('[{"role": "user", "content": "q"},'
            ' {"type": "message", "content": ""},'
            ' {"type": "tool_call", "cmd": "grep(pattern=\'03/16/2016\', path=\'/d/a.txt\')",'
            '  "obs": "hit"}]')
    with tempfile.TemporaryDirectory() as root:
        keys, traces = [], []
        for epoch, response in (("step_0001", "first attempt"), ("step_0003", "third attempt")):
            pred = os.path.join(root, epoch, "rollout", "predictions")
            os.makedirs(os.path.join(pred, "UID0019"))
            for name, text in (("conversation.json", conv),
                               ("target_user_prompt.txt", "## Question\nq\n")):
                with open(os.path.join(pred, "UID0019", name), "w", encoding="utf-8") as f:
                    f.write(text)
            res = os.path.join(root, epoch, "rollout", "results.jsonl")
            with open(res, "w", encoding="utf-8") as f:
                f.write(json.dumps({"id": "UID0019", "response": response, "hard": 0}) + "\n")
            prov = R.attempt_provenance(pred, "UID0019", res)
            keys.append(prov["attempt_key"])
            traces.append(prov["trace_sha256"])
            assert prov["response_metadata_availability"] == "unavailable"
            assert prov["row_has_termination_cause"] is False
        assert traces[0] == traces[1], "identical records, identical digest - the collision is real"
        assert keys[0] != keys[1], "the attempt key must still separate them"
    # and a row written after amendment A-1 says its metadata is live
    with tempfile.TemporaryDirectory() as root:
        pred = os.path.join(root, "step_0004", "rollout", "predictions")
        os.makedirs(os.path.join(pred, "UID0019"))
        for name, text in (("conversation.json", conv), ("target_user_prompt.txt", "q")):
            with open(os.path.join(pred, "UID0019", name), "w", encoding="utf-8") as f:
                f.write(text)
        res = os.path.join(root, "step_0004", "rollout", "results.jsonl")
        with open(res, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "UID0019", "response": "x",
                                "termination_cause": "empty_reply_content_null",
                                "response_metadata": {"finish_reason": "stop"}}) + "\n")
        prov = R.attempt_provenance(pred, "UID0019", res)
        assert prov["response_metadata_availability"] == "live"
        assert prov["row_has_termination_cause"] is True
        assert prov["epoch"] == 4 and prov["epoch_source"].startswith("parsed")


def test_a_frozen_model_records_its_parent_and_its_freeze_epoch_and_cannot_be_edited():
    """`parent_sha256` was null in all seven campaign models and `epoch` is the last CHANGE, not the
    freeze -- every arm-C model says 1 although they were frozen at epochs 2, 3 and 4."""
    IR = _research("oqa_lc_ir")
    import tempfile
    base = {"schema": "oqa.lc.model.v1", "version": 2, "epoch": 1, "contracts": [],
            "assumptions": [], "vocab": "v", "decisions": [], "evidence_items": []}
    with tempfile.TemporaryDirectory() as root:
        # no kwargs: byte-identical behaviour and the same digest as before the amendment
        p0 = os.path.join(root, "m0.json")
        sha0 = IR.freeze(dict(base), p0)
        assert sha0 == IR.model_sha(base)
        m0 = json.load(open(p0, encoding="utf-8"))
        assert "frozen_at_epoch" not in m0 and not m0.get("parent_sha256")
        # with them: recorded, and covered by the digest, so load_frozen verifies them
        p1 = os.path.join(root, "m1.json")
        sha1 = IR.freeze(dict(base), p1, parent_sha256=sha0, frozen_at_epoch=4)
        m1 = IR.load_frozen(p1)
        assert m1["parent_sha256"] == sha0 and m1["frozen_at_epoch"] == 4
        assert m1["epoch"] == 1, "the existing field keeps its meaning: the last change"
        assert sha1 != sha0, "provenance is part of the content"
        # editing the provenance of a frozen model is detected
        os.chmod(p1, 0o644)
        d = json.load(open(p1, encoding="utf-8"))
        d["frozen_at_epoch"] = 2
        with open(p1, "w", encoding="utf-8") as f:
            json.dump(d, f)
        with pytest.raises(RuntimeError):
            IR.load_frozen(p1)


def test_the_in_sample_note_tests_the_attempt_when_the_model_records_them():
    """96 of 96 reports per arm carried the note when it was true of 24. The old wording is kept for a
    model that records only item ids, so no historical report's text moves."""
    REPORT = _research("oqa_lc_report")
    model = {"version": 2, "sha256": "s" * 64, "contracts": [], "assumptions": [],
             "evidence_items": ["UID0019"], "evidence_scope": "the shared epoch-1 rollout"}
    old = REPORT.render_action({"item": "UID0019", "attempts": []}, model)
    assert "NOTE: this item's own trace is part of the evidence" in old

    model2 = dict(model, evidence_attempts=[{"trace_key": "UID0019/e1", "trace_sha256": "a" * 64,
                                             "collection": "/c/e1"}])
    same = REPORT.render_action({"item": "UID0019", "trace_key": "UID0019/e1",
                                 "trace_sha256": "a" * 64, "attempts": []}, model2)
    assert "NOTE: this very attempt is part of the evidence" in same
    fresh = REPORT.render_action({"item": "UID0019", "trace_key": "UID0019/e4",
                                 "trace_sha256": "b" * 64, "attempts": []}, model2)
    assert "NOTE:" not in fresh, "a fresh attempt of a seen question id must NOT be called in-sample"
    collide = REPORT.render_action({"item": "UID0019", "trace_key": "UID0019/e3",
                                   "trace_sha256": "a" * 64, "attempts": []}, model2)
    assert "possibly in-sample" in collide, "a digest collision is reported as what it is"


# ── the regression that matters: no historical report text moves ──────────────

@pytest.mark.skipif(not CAMPAIGN or not os.path.isdir(CAMPAIGN),
                    reason="needs the frozen campaign directory in OQA_CAMPAIGN_ROOT")
@pytest.mark.parametrize("arm,item,epoch,condition,bytes_preserved", [
    # MEASURED, not assumed (job 66553105, oqa_report_det.py, 8 runs within one process and 8 across
    # processes with fresh hash seeds): report generation is DETERMINISTIC, and UID0019's report is
    # byte-identical to the historical one in all 16 runs. So this case pins a genuine invariant.
    ("B", "UID0019", 3, "LC_ACTION", True),
    # AND THIS ONE GENUINELY MOVES, deterministically: 51f046e063a7f2ce against the historical
    # 8b90446cecdc935c, the same digest in all 16 runs. C-1's selection gate legitimately changes which
    # source cell is reported, and the historical report was generated under the tie-break C-1 refuses.
    #
    # I BRIEFLY PINNED THIS TO True AND THAT WAS WRONG. I read an earlier job's failure of THIS test as
    # evidence that C-2 had restored the bytes - but the assertion that failed there was not the digest
    # one at all, it was "the stage group count keeps its historical value" (17 against 14). I inferred
    # a fact from a test NAME without reading its assertion. The digest has differed from the
    # historical one continuously since C-1.
    #
    # The stage-group count is the part that tells a story: C-2 took it 14 -> 17 by restoring bindings
    # that then produced FALSE period accusations, and C-3 returned it to 14 by making an undetermined
    # year UNKNOWN instead of VIOLATED. That assertion passes again, which is independent support for
    # C-3 being the right repair.
    ("C", "UID0014", 3, "LC_STAGE", False),
])
def test_regenerating_a_real_historical_report_reproduces_its_bytes(
        arm, item, epoch, condition, bytes_preserved):
    """The two worked examples of OQA-FOR-1, regenerated with the amended code.

    Amendment A-1 promised the changes were metadata-only: the report TEXT the optimizer read had to be
    byte-identical. These are the campaign's only violated action forecast and its only report with
    violated stage obligations, so if anything moved it would move here - and under C-1 one of them does.

    WHAT IS STILL ASSERTED FOR BOTH. The model digest, the trace digest and the action forecasts are
    unchanged, because C-1 touches only which SOURCE CELL a value is bound to. A change in any of those
    would mean C-1 had reached further than its amendment claims.
    """
    R = _research("oqa_reports")
    gen = os.path.join(CAMPAIGN, f"arm_{arm}", "stage_reports", "GENERATED.jsonl")
    recs = [json.loads(l) for l in open(gen, encoding="utf-8") if l.strip()]
    rec = next(r for r in recs
               if r["id"] == item and f"step_{epoch:04d}" in str(r.get("prediction_dir", "")))
    pred = rec["prediction_dir"]
    results = os.path.join(os.path.dirname(pred), "results.jsonl")
    model = os.path.join(CAMPAIGN, "contracts",
                         "model_M0.json" if epoch == 2 else
                         os.path.join(f"arm_{arm}", f"model_arm{arm}_epoch{epoch - 1:02d}.json"))
    question = next(json.loads(l)["question"] for l in open(results, encoding="utf-8")
                    if l.strip() and json.loads(l)["id"] == item)
    text, new_rec = R.build_one_lc(pred, item, question, results, model, condition=condition)
    now = hashlib.sha256(text.encode()).hexdigest()
    if bytes_preserved:
        assert now == rec["report_sha256"], (
            f"the rendered report text moved and this case is pinned as UNCHANGED: "
            f"historical {rec['report_sha256'][:16]}, now {now[:16]}. Either a change reached further "
            f"than its amendment claims, or this expectation needs re-pinning deliberately.")
    else:
        assert now != rec["report_sha256"], (
            "this case is pinned as CHANGED by amendment C-1, but the bytes are identical, so the "
            "C-1 selection gate is not reaching the report at all")
    assert new_rec["model_sha256"] == rec["model_sha256"]
    assert new_rec["trace_sha256"] == rec["trace_sha256"]
    assert new_rec["action_forecasts"] == rec["action_forecasts"]
    # and the new fields say what the old constant could not
    if condition == "LC_ACTION":
        assert rec["n_distinct_diagnoses"] == 0, "the historical record's constant"
        assert new_rec["diagnosis_counts"]["model_refutations"] == 1, (
            "arm B's one violated forecast is now counted as what it is")
        assert new_rec["diagnosis_counts"]["execution_diagnoses"] == 0
        assert new_rec["n_stage_diagnosis_groups"] is None
    else:
        assert new_rec["n_stage_diagnosis_groups"] == rec["n_distinct_diagnoses"], (
            "the stage group count keeps its historical value, under its own name")
        assert new_rec["diagnosis_counts"]["execution_diagnoses"] > 0
    assert new_rec["model_parent_sha256"] == "unavailable", "a historical model has none, and says so"
    assert new_rec["model_frozen_at_epoch"] == "unavailable"
    assert new_rec["attempt"]["response_metadata_availability"] == "unavailable"


# ── OQA-HTTP-1: the conversion defect, and the smallest correction ────────────

class _MsgReasoning:
    """The message the provider really returns: content null, no tool calls, `reasoning` holding the text.

    Verbatim shape from job 66340531, replaying arm B epoch 2's recorded request for UID0017:
        choices[0].finish_reason = "stop"
        choices[0].message keys  = annotations, audio, content, function_call, reasoning, refusal, role
        message.content          = null
        message.reasoning        = 944 chars ending "<answer>[10102000000, 4.73]</answer>"
    The SDK exposes `reasoning` only through `model_extra`, which is why `getattr(msg, "reasoning")` is
    not enough on its own.
    """

    def __init__(self, text):
        self.content = None
        self.tool_calls = []
        self.refusal = None
        self.model_extra = {"reasoning": text}

    def model_dump(self):
        return {"role": "assistant", "content": None, "refusal": None, **self.model_extra}


_REAL_REASONING = ("Based on my analysis of the Treasury Bulletin August 1982 document, I found:\n\n"
                   "   - Total bids = $10,102 million\n   - Noncash rollover tenders = $478 million\n"
                   "   - Percentage = (478 / 10,102) x 100 = 4.7319...% = 4.73%\n\n"
                   "<answer>[10102000000, 4.73]</answer>")


def test_the_providers_reasoning_field_is_found_and_reported(monkeypatch):
    """`reasoning_content` was null in all 7,909 logged attempts. The field is `reasoning`."""
    monkeypatch.delenv("SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK", raising=False)
    m = _MsgReasoning(_REAL_REASONING)
    text, field = COMMON.reasoning_text(m)
    assert field == "reasoning" and text == _REAL_REASONING
    out = COMMON.compat_message_from_chat_message(m, _Resp(m, "stop"), _Choice(m, "stop"))
    md = out.metadata
    assert md["reasoning_field_name"] == "reasoning"
    assert md["reasoning_has_answer_tag"] is True
    assert md["reasoning_content_chars"] == len(_REAL_REASONING)
    assert md["content_is_null"] is True


def test_the_historical_behaviour_is_the_default(monkeypatch):
    """Without the fallback the loop ends the episode, exactly as the campaign did. Nothing is repaired
    silently, so the frozen campaign's recorded behaviour stays reproducible."""
    monkeypatch.delenv("SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK", raising=False)
    m = _MsgReasoning(_REAL_REASONING)
    out = COMMON.compat_message_from_chat_message(m, _Resp(m, "stop"), _Choice(m, "stop"))
    assert out.content == "", "the historical conversion yields an empty string"
    assert "content_fallback_applied" not in out.metadata
    from skillopt.envs.officeqa.rollout import _no_answer_cause
    assert _no_answer_cause(out.content, out.metadata, "") == "empty_reply_content_null"


def test_the_smallest_correction_recovers_the_answer_that_was_already_in_the_response(monkeypatch):
    """With the fallback on, the text the provider sent is used and the answer extracts cleanly.

    This is proved from the shape of a REAL captured response, so it does not depend on a new request.
    """
    monkeypatch.setenv("SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK", "reasoning")
    m = _MsgReasoning(_REAL_REASONING)
    out = COMMON.compat_message_from_chat_message(m, _Resp(m, "stop"), _Choice(m, "stop"))
    assert out.content == _REAL_REASONING
    assert out.metadata["content_taken_from"] == "reasoning"
    assert out.metadata["content_fallback_applied"] is True
    from skillopt.envs.officeqa.rollout import _extract_answer
    assert _extract_answer(out.content) == "[10102000000, 4.73]"
    # and the loop would now take the answer branch instead of ending the episode
    assert "<answer>" in out.content.lower()


def test_the_fallback_never_overrides_content_or_a_tool_call(monkeypatch):
    """It is a fallback, not a preference: real content and a tool call both win."""
    monkeypatch.setenv("SKILLOPT_OPENAI_COMPATIBLE_CONTENT_FALLBACK", "1")

    class _Both(_MsgReasoning):
        def __init__(self):
            super().__init__("some reasoning <answer>9</answer>")
            self.content = "<answer>7</answer>"

    out = COMMON.compat_message_from_chat_message(_Both(), _Resp(_Both(), "stop"),
                                                  _Choice(_Both(), "stop"))
    assert out.content == "<answer>7</answer>"
    assert "content_fallback_applied" not in out.metadata

    class _Tooled(_MsgReasoning):
        def __init__(self):
            super().__init__("thinking about it <answer>9</answer>")
            self.tool_calls = [_TC("grep")]

    out2 = COMMON.compat_message_from_chat_message(_Tooled(), _Resp(_Tooled(), "tool_calls"),
                                                   _Choice(_Tooled(), "tool_calls"))
    assert out2.content == "", "a tool-calling turn keeps the loop's tool branch"
    assert "content_fallback_applied" not in out2.metadata
    assert out2.metadata["reasoning_field_name"] == "reasoning"
