"""RP-1 repair driver (protocol docs/research/RP1_PROTOCOL.md). Cluster CPU job; DeepSeek through rp_llm (8 / $1.00).

  rp_run.py --input DIR --out OUT [--lean-bin BIN] [--job-ledger JL --call-log CL] | [--fake MODE_A,MODE_B]

DIR = the frozen repair input (rp_select.py): VX1_SELECTION.json, learning/ (the 84 VX-1 learning records),
cx_view.json (the counterexample, learner view B5-e01), START_MODEL_EXT2_M4.json. Nothing else is read.

Two INDEPENDENT attempts A and B start from the SAME frozen model (EXT2 M4, sha checked) and receive the SAME first
request, built by the UNCHANGED VX-1 interface (vx_prompt.build_request as "Batch 5 of at most 5": the current model,
the new record CX with the frozen model's committed predictions and verdict discrepancies, and the after-step verdict
summary of every earlier learning record). Separate calls, separate contexts, tags RP1-A/... and RP1-B/....
Per attempt, at most TWO proposal/check rounds:
  round 1  the request (one resend only if the provider returned no reply);
  round 2  EITHER a parse retry (round 1 unreadable) OR the check feedback, given only when round 1 left a rejected
           operation or an item the gate made inactive (refuted); the feedback lists exactly those decisions with the
           code's reasons (vx_prompt.feedback_request's format; RP-1 adds the refuted items, declared in the protocol).
Gate = vx_learn.apply_proposal (unchanged) over ALL repair evidence: the 84 learning records + CX.
Each candidate is frozen; every earlier record is rechecked; Lean export + compile; report bound to the digests.
--fake uses canned local proposers (driver test only; never a study result).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import copy
import datetime
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vx_check as K  # noqa: E402
import vx_ir as IR  # noqa: E402
import vx_lean as XL  # noqa: E402
import vx_learn as LR  # noqa: E402
import vx_prompt as P  # noqa: E402
import vx_run as VR  # noqa: E402   (VX-1 helpers, unchanged: load_learning, earlier_text, recheck, lean_compile, ...)

START_SHA = "55da91f31189d84b3c84f5308e63dde697037ed445e4c78b9eaf1058a67b469c"
STREAMS = ("A", "B")
BATCH, N_BATCHES = 5, 5
ATT_CAP = int(os.environ.get("RP_ATT_CAP", "8"))
# material that must never reach the repair learner (beyond vx_prompt.FORBIDDEN): the other stream's solution, the
# manual repair, the audit's identifiers and the source trace identity
RP_FORBIDDEN = ["EXT1", "EXT2", "34d9fda0", "55da91f3", "ORD2C", "ord2_candidate", "manual repair", "ReadingR3",
                "tr:0215", "bbd540c89f6f", "Pencil-None-DeskLamp-307", "trial_T", "use.effect", "go.effect",
                "take.effect", "move.effect", "post-hoc", "posthoc"]


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


jdump = VR.jdump


def rp_leak_check(msgs: list) -> None:
    P.leak_check(msgs)
    for m in msgs:
        for f in RP_FORBIDDEN:
            if f in m["content"]:
                raise P.LeakError(f"repair request contains forbidden material {f!r}: nothing sent")


def feedback_request(prev_msgs: list, reply: str, decisions: list) -> list:
    """vx_prompt.feedback_request's text, listing rejected operations AND items the gate made inactive."""
    rej = [d for d in decisions if d.get("decision") in ("rejected", "refuted")]
    lines = ["The code checked your proposal against all records so far. Rejected operations, and items the code made "
             "inactive (refuted) because a counterexample remains:"]
    for d in rej:
        lines.append(f"- {d.get('type')} {d.get('op')} {d.get('target') or d.get('name') or ''}: "
                     f"{'; '.join(d.get('why') or [])}")
    lines += ["Accepted operations are already applied. You may send corrected versions of the rejected operations "
              "(or none). Same JSON format; do not repeat accepted operations."]
    msgs = prev_msgs + [{"role": "assistant", "content": reply or ""}, {"role": "user", "content": "\n".join(lines)}]
    rp_leak_check(msgs)
    return msgs


# ---- the learner call ----------------------------------------------------------------------------------------------
class Chat:
    def __init__(self, a, fake):
        self.fake = fake
        self.attempts = 0
        if fake:
            return
        import rp_llm as RL
        self.RL = RL
        client = None
        if os.environ.get("RP_OFFLINE_REPLAY") == "1":   # local check only: cached replies or a logged refusal
            class _Refuse:
                base_url = RL.BASE_URL

                class chat:
                    class completions:
                        @staticmethod
                        def create(**kw):
                            raise RuntimeError("offline replay: not in the cache, nothing sent")
            client = _Refuse()
        self.tr = RL.Transport(RL.BASE_URL, RL.MODEL, a.job_ledger, a.call_log, os.path.join(a.out, "cache"),
                               client=client, max_in_flight=2)

    def __call__(self, msgs, tag) -> dict:
        self.attempts += 1
        if self.fake:
            return fake_reply(self.fake[tag.split("/")[0][-1]], msgs, tag)
        r = self.tr.chat(msgs, self.RL.SETTINGS, stage="rp_learner", tag=tag, timeout_s=self.RL.TIMEOUT_S)
        if r.get("cached"):
            self.attempts -= 1
        return r

    def used(self) -> int:
        return self.attempts if self.fake else self.tr.stats["attempts"]


def fake_reply(mode, msgs, tag) -> dict:
    """Driver test only: canned proposals (never a study result)."""
    cur = [json.loads(x.split("\n", 1)[0]) for x in msgs[1]["content"].split("  json: ")[1:]]
    cur = next(c for c in cur if c.get("kind") == "completes")
    drop = dict(cur, condition=[cl for cl in cur["condition"] if cl[0]["atom"] != "released"])
    if mode == "revise" or (mode == "keep_then_revise" and tag.endswith("/r2")):
        prop = {"contract_ops": [{"op": "revise", "target": "completes.any.1", "contract": drop, "reason": "fake",
                                  "witnesses": ["B5-e01@27"]}]}
    elif mode == "retire":
        prop = {"contract_ops": [{"op": "retire", "target": "completes.any.1", "reason": "fake"}]}
    elif mode == "garbage":
        return {"ok": True, "content": "no json here", "fake": True}
    else:
        prop = {"contract_ops": [{"op": "keep", "target": "completes.any.1", "reason": "fake"}]}
    return {"ok": True, "content": "```json\n" + json.dumps(prop) + "\n```", "finish_reason": "stop", "fake": True}


# ---- main -------------------------------------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lean-bin")
    ap.add_argument("--job-ledger")
    ap.add_argument("--call-log")
    ap.add_argument("--fake")
    a = ap.parse_args()
    fake = dict(zip(STREAMS, a.fake.split(","))) if a.fake else None
    os.makedirs(a.out, exist_ok=True)
    # ---- inputs (frozen) ------------------------------------------------------------------------------------------
    sel = json.load(open(os.path.join(a.input, "VX1_SELECTION.json")))
    batches, info = VR.load_learning(sel, os.path.join(a.input, "learning"))
    seen = [e for b in batches for e in b]
    assert info["missing"] == [] and info["empty_excluded"] == [], info
    if not fake:
        assert len(seen) == 84, "the repair evidence must be the 84 VX-1 learning records"
    m4 = IR.load_frozen(os.path.join(a.input, "START_MODEL_EXT2_M4.json"))
    if not fake:
        assert m4["sha256"] == START_SHA, "start model is not EXT2 M4"
    cxv = json.load(open(os.path.join(a.input, "cx_view.json")))
    cx = {"episode_id": cxv["episode_id"], "src": "CX", "task": cxv["task"], "task_type": cxv["task_type"],
          "outcome": cxv["outcome"], "steps": cxv["steps"]}
    assert cx["episode_id"] == "B5-e01" and cx["outcome"] == "success"
    ev = K.Evidence(seen + [cx])
    # ---- 1. predictions of the frozen model on CX, saved BEFORE the request is built -------------------------------
    pre = K.predict_episode(m4, VR.outcome_free(cx))
    post = K.post_verdicts(m4, VR.outcome_free(cx) | {"outcome": None})
    pj = {"prior_sha256": m4["sha256"], "made_utc": now(), "pre": {cx["episode_id"]: pre},
          "post": {cx["episode_id"]: [{x: v[x] for x in ("verdict", "deps", "unknown")} for v in post]}}
    jdump(pj, os.path.join(a.out, "PREDICTIONS.json"))
    pred_sha = VR.XV.sha_file(os.path.join(a.out, "PREDICTIONS.json"))
    msgs = P.build_request("EXT", BATCH, N_BATCHES, m4, [VR.learner_view(cx)], {cx["episode_id"]: pre},
                           {cx["episode_id"]: post}, {"text": VR.earlier_text(m4, seen)})
    rp_leak_check(msgs)
    jdump({"messages": msgs, "sha256": sha(json.dumps(msgs)), "built_utc": now(), "predictions_sha256": pred_sha,
           "system_prompt_sha256": P.XV.sha(msgs[0]["content"]), "same_for": list(STREAMS)},
          os.path.join(a.out, "REQUEST_r1.json"))
    jdump({"schema": "rp.inputs.v1", "selection_sha256": sel["sha256"], "records": info, "start_model": m4["sha256"],
           "cx_view_sha256": VR.XV.sha_file(os.path.join(a.input, "cx_view.json")), "prompts": P.prompt_diff(),
           "vocab": VR.XV.vocab_digest("EXT"), "streams": list(STREAMS), "att_cap": ATT_CAP, "fake": bool(fake),
           "utc": now()}, os.path.join(a.out, "INPUTS.json"))
    chat = Chat(a, fake)
    st = {s: {"dir": os.path.join(a.out, s)} for s in STREAMS}
    stop = None

    def call(msgs_, tag):
        res = chat(msgs_, tag)
        if not res.get("ok") and chat.used() + 1 <= ATT_CAP:
            res = chat(msgs_, tag + "#retry")
        return res
    # ---- 2. round 1 (both attempts, concurrently) ------------------------------------------------------------------
    try:
        with cf.ThreadPoolExecutor(max_workers=2) as ex:
            futs = {s: ex.submit(call, msgs, f"RP1-{s}/b{BATCH}/r1") for s in STREAMS}
            for s in STREAMS:
                st[s]["r1"] = futs[s].result()
    except BaseException as e:  # noqa: BLE001  ProviderStop / BudgetStop / EndpointStop
        stop = f"{type(e).__name__}: {e}"
        for s in STREAMS:
            st[s].setdefault("r1", {"ok": False, "error": stop})
    for s in STREAMS:
        try:
            st[s]["prop1"] = P.parse_reply(st[s]["r1"].get("content") or "")
        except Exception as e:  # noqa: BLE001
            st[s]["prop1"], st[s]["perr"] = None, str(e)[:200]
    # parse retry = that attempt's round 2 (only if a reply arrived but was unreadable)
    need = [s for s in STREAMS if st[s]["prop1"] is None and st[s]["r1"].get("ok")]
    if need and not stop and chat.used() + len(need) <= ATT_CAP:
        for s in need:
            m2 = msgs + [{"role": "assistant", "content": st[s]["r1"].get("content") or ""},
                         {"role": "user", "content": VR.PARSE_RETRY.format(err=st[s]["perr"])}]
            rp_leak_check(m2)
            try:
                st[s]["r1p"] = chat(m2, f"RP1-{s}/b{BATCH}/parse")
                st[s]["prop1"] = P.parse_reply(st[s]["r1p"].get("content") or "")
            except BaseException as e:  # noqa: BLE001
                st[s]["perr2"] = f"{type(e).__name__}: {str(e)[:200]}"
            st[s]["round2_used_by_parse_retry"] = True
    for s in STREAMS:
        try:
            m1, d1 = LR.apply_proposal(m4, st[s]["prop1"] or {}, ev, BATCH, "learner" if st[s]["prop1"] else "none")
        except Exception as e:  # noqa: BLE001
            st[s]["apply_error"] = f"{type(e).__name__}: {str(e)[:300]}"
            m1, d1 = LR.apply_proposal(m4, {}, ev, BATCH, "none")
        m1["rp"] = {"attempt": s, "round": 1, "predictions_sha256": pred_sha}
        p1 = os.path.join(st[s]["dir"], "M5_round1.json")
        os.makedirs(st[s]["dir"], exist_ok=True)
        IR.freeze(m1, p1)
        st[s]["m1"], st[s]["d1"] = IR.load_frozen(p1), d1
    # ---- 3. round 2: check feedback (rejected or refuted items), equal for both attempts or for none ----------------
    want = [s for s in STREAMS if not st[s].get("round2_used_by_parse_retry") and st[s]["prop1"] is not None
            and any(x.get("decision") in ("rejected", "refuted") for x in st[s]["d1"])]
    grant = bool(want) and not stop and chat.used() + len(want) <= ATT_CAP
    if grant:
        try:
            with cf.ThreadPoolExecutor(max_workers=2) as ex:
                futs = {}
                for s in want:
                    reply = (st[s].get("r1p") or st[s]["r1"]).get("content")
                    st[s]["msgs2"] = feedback_request(msgs, reply, st[s]["d1"])
                    futs[s] = ex.submit(call, st[s]["msgs2"], f"RP1-{s}/b{BATCH}/r2")
                for s in want:
                    st[s]["r2"] = futs[s].result()
        except BaseException as e:  # noqa: BLE001
            stop = f"{type(e).__name__}: {e}"
    # ---- 4. candidates, recheck, Lean, report ----------------------------------------------------------------------
    views = [VR.learner_view(e) for e in seen + [cx]]
    lean = {}
    lp = os.path.join(a.out, "lean", "Rp_EXT2M4_on_repair_evidence.lean")
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    try:
        src, side = XL.export(m4, views, "RP-1 start model EXT2 M4 on the repair evidence (84 learning records + CX)")
        open(lp, "w").write(src)
        jdump(side, lp.replace(".lean", ".sidecar.json"))
        lean["EXT2_M4"] = VR.lean_compile(a.lean_bin, lp)
    except Exception as e:  # noqa: BLE001
        lean["EXT2_M4"] = {"compiled": False, "why": f"export failed: {type(e).__name__}: {str(e)[:300]}"}
    done = {}
    for s in STREAMS:
        m, dec = copy.deepcopy(st[s]["m1"]), list(st[s]["d1"])
        if st[s].get("r2"):
            try:
                p2 = P.parse_reply(st[s]["r2"].get("content") or "")
                st[s]["prop2"] = p2
                try:
                    m, d2 = LR.apply_proposal(st[s]["m1"], p2, ev, BATCH, "learner-round2")
                except Exception as e:  # noqa: BLE001
                    st[s]["apply2_error"] = f"{type(e).__name__}: {str(e)[:300]}"
                    d2 = []
                dec += d2
            except Exception as e:  # noqa: BLE001
                st[s]["r2_parse_error"] = str(e)[:200]
        m["version"] = m4["version"] + 1
        m["parent_sha256"] = m4["sha256"]
        m["decisions"] = dec
        m["rp"] = {"attempt": s, "round": 2 if st[s].get("r2") else 1, "predictions_sha256": pred_sha,
                   "round1_model_sha256": st[s]["m1"]["sha256"]}
        cp = os.path.join(st[s]["dir"], "CANDIDATE.json")
        IR.freeze(m, cp)
        m = IR.load_frozen(cp)
        rk = VR.recheck(m4, m, seen + [cx])
        jdump(rk, os.path.join(st[s]["dir"], "RECHECK.json"))
        lpath = os.path.join(st[s]["dir"], "lean", f"Rp_{s}_candidate.lean")
        os.makedirs(os.path.dirname(lpath), exist_ok=True)
        try:
            src, side = XL.export(m, views, f"RP-1 attempt {s} candidate on the repair evidence (84 learning records + CX)")
            open(lpath, "w").write(src)
            jdump(side, lpath.replace(".lean", ".sidecar.json"))
            lr = VR.lean_compile(a.lean_bin, lpath)
        except Exception as e:  # noqa: BLE001
            lr = {"compiled": False, "why": f"export failed: {type(e).__name__}: {str(e)[:300]}"}
        jdump(lr, os.path.join(st[s]["dir"], "lean", "RECEIPT.json"))
        rep = VR.report_md(f"RP1-{s}", m["vocab"], m, lr, dec, rk).replace("# VX-1 learned model", "# RP-1 repair candidate")
        open(os.path.join(st[s]["dir"], "REPORT.md"), "w").write(rep)
        jdump({"model_sha256": m["sha256"], "round1_model_sha256": st[s]["m1"]["sha256"], "lean_sha256": lr.get("lean_sha256"),
               "lean_rc": lr.get("rc"), "report_sha256": sha(rep), "predictions_sha256": pred_sha,
               "request_r1_sha256": sha(json.dumps(msgs))}, os.path.join(st[s]["dir"], "BIND.json"))
        jdump({k: st[s].get(k) for k in ("r1", "r1p", "r2", "prop1", "prop2", "perr", "perr2", "r2_parse_error",
                                         "apply_error", "apply2_error", "round2_used_by_parse_retry")}
              | {"round2_wanted": s in want, "round2_granted": grant and s in want,
                 "messages_r2": st[s].get("msgs2")}, os.path.join(st[s]["dir"], "CALLS.json"))
        done[s] = {"candidate": m["sha256"], "round1": st[s]["m1"]["sha256"], "lean_rc": lr.get("rc"),
                   "active": [IR.render(c) for c in IR.active(m)], "assumptions": m["assumptions"],
                   "decisions": [(d["type"], d["op"], d.get("target") or d.get("name"), d.get("decision")) for d in dec],
                   "regressions": rk["n_regressions"], "improvements": rk["n_improvements"]}
        print(json.dumps({"attempt": s, **done[s]})[:1500], flush=True)
    jdump({"schema": "rp.repair_done.v1", "stop": stop, "attempts_used": chat.used(), "att_cap": ATT_CAP,
           "round2_wanted": want, "round2_granted": grant, "start_model": m4["sha256"], "lean_start_model": lean["EXT2_M4"],
           "attempts": done, "utc": now()}, os.path.join(a.out, "REPAIR_DONE.json"))
    print("attempts used", chat.used(), "stop", stop)
    return 0 if stop is None else (8 if "Budget" in stop or "budget" in stop else 32)


if __name__ == "__main__":
    raise SystemExit(main())
