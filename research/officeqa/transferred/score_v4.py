"""v4 scoring rules (amendment A3 section 3.6; frozen with the implementation in FREEZE_IMPL_v4 before the replay).

Unit: one OCCURRENCE of a message at its prefix (PRE = earlier steps + the selected command; POST adds its observation).
Every occurrence is judged by progress_v4.judge with the shared reading of ITS prefix (identical judge for v3 and v4).
A PROBLEM is (episode, family, instance-or-receptacle). Per variant and problem:
  first_valid   the first VALID occurrence of a WARNING / ADVICE (EVENT occurrences count as valid observations;
                NOTE and POSITIVE are never problem warnings); an earlier invalid occurrence never hides it
Comparison of an extended variant X over its base B (S over P, C over S), per problem:
  ADDITIONAL            X has a valid warning, B has none
  EARLIER               both valid, X's first valid prefix is earlier
  TIE                   same first valid prefix
  LATER                 X's first valid prefix is later (cannot happen when X contains B; reported if it does)
  MORE_ACTIONABLE       X's first valid occurrence is ADVICE while B's first valid occurrence is not ADVICE (or B has
                        only a NOTE / EVENT for that problem); counted in addition to the timing class
  MORE_SPECIFIC         (shared problems only) X issues a VALID message of a category B never issues for that problem,
                        at a prefix where B has no valid message for it (first such occurrence recorded); counted in
                        addition to the timing class. v6: a new category only at prefixes where B already speaks is a
                        SAME_PREFIX_ANNOTATION (reported, not credited). REDUNDANT = distinct valid prefixes - 1.
  REDUNDANT             valid occurrences of X after its first valid one (repeats) — counted, not credited
  INVALID               occurrences judged PREMATURE / IRRELEVANT / UNSUPPORTED (per variant), and CONTRADICTED ones
  LIFECYCLE             for each ADVICE problem: the step at which the advice was followed or became obsolete
Eventual success never makes a warning invalid; no later contradiction never makes it useful.
"""
from __future__ import annotations

import progress_v4 as PV
import stage_common as SC


def structural_check(base, top, label):
    """top extends base: every base finding is in top with the same first prefix (raises otherwise)"""
    tmap = {f["key"]: f for f in top}
    bad = [f["key"] for f in base if f["key"] not in tmap or tmap[f["key"]]["first"] != f["first"]]
    if bad:
        raise SystemExit(f"{label}: base findings changed in the extended report: {bad}")
    return {"ties": len(base), "n_top_only": len(top) - len(base)}


def resolve(f, R, P, type_of):
    """end-of-record status (shared; v3's resolve for every v3 category; the two new categories mapped explicitly)"""
    import stage_study as S3M
    if f["cat"] == "revisit_known_contents":
        return S3M.resolve(dict(f, cat="find_known_contents"), R, P, type_of)
    if f["cat"] == "known_instance_note":
        return "note"
    return S3M.resolve(f, R, P, type_of)


def pk(t, phase):
    return (t, 0 if phase == "pre" else 1)


def occ_table(findings, P, sp, type_of, cache):
    """all occurrences of a variant's final findings, judged at their own prefixes"""
    rows = []
    for f in findings:
        for o in PV.occurrences(f):
            t = o["t"]
            if t not in cache:
                cache[t] = SC.read_prefix(sp, P[:t], type_of)
            verdict, why = PV.judge(f, o, cache[t], P, type_of)
            rows.append({"key": f["key"], "cat": f["cat"], "kind": PV.kind_of(f["cat"]),
                         "tier": f.get("tier", "primary"), "layer": f.get("layer"), "t": t, "phase": o["phase"],
                         "identity": list(PV.identity(f, o)), "verdict": verdict, "why": why,
                         "contradicted": PV.contradicted(f, o, P), "occ": o})
    rows.sort(key=lambda r: (pk(r["t"], r["phase"]), r["key"]))
    return rows


def problems(rows):
    """{identity: {first_valid, first_any, n_valid, n_invalid, kinds}} over problem-warning occurrences"""
    out = {}
    for r in rows:
        if r["kind"] in ("NOTE", "POSITIVE"):
            continue
        idn = tuple(r["identity"])
        p = out.setdefault(idn, {"first_any": None, "first_valid": None, "n_valid": 0, "n_invalid": 0,
                                 "n_contradicted": 0, "invalid": []})
        if p["first_any"] is None:
            p["first_any"] = r
        if r["verdict"] == "VALID" and not r["contradicted"]:
            p["n_valid"] += 1
            if p["first_valid"] is None:
                p["first_valid"] = r
        else:
            p["n_invalid"] += 1
            p["n_contradicted"] += int(r["contradicted"])
            p["invalid"].append({k: r[k] for k in ("key", "t", "phase", "verdict", "why", "contradicted")})
    return out


def notes(rows):
    return [r for r in rows if r["kind"] == "NOTE"]


def compare(base_rows, top_rows):
    B, X = problems(base_rows), problems(top_rows)
    out = []
    for idn in sorted(set(B) | set(X), key=lambda x: (str(x[0]), str(x[1]))):
        b, x = B.get(idn), X.get(idn)
        bf = b["first_valid"] if b else None
        xf = x["first_valid"] if x else None
        if xf is None:
            if bf is not None:
                out.append({"identity": list(idn), "class": "BASE_ONLY", "base": _brief(bf), "top": None})
            continue
        if bf is None:
            cls = "ADDITIONAL"
        else:
            a, c = pk(xf["t"], xf["phase"]), pk(bf["t"], bf["phase"])
            cls = "EARLIER" if a < c else ("TIE" if a == c else "LATER")
        actionable = xf["kind"] == "ADVICE" and (bf is None or bf["kind"] != "ADVICE")
        # MORE_SPECIFIC: a VALID message of a category the base never issues for this problem (e.g. S's
        # redo_mismatched on a wrong-type problem P already reported); credited as guidance, not as earlier timing
        bcats = {r["cat"] for r in base_rows if tuple(r["identity"]) == idn}
        bpre = {pk(r["t"], r["phase"]) for r in base_rows if tuple(r["identity"]) == idn and r["verdict"] == "VALID"}
        new = [r for r in top_rows if tuple(r["identity"]) == idn and r["cat"] not in bcats and r["verdict"] == "VALID"
               and not r["contradicted"] and r["kind"] not in ("NOTE", "POSITIVE")]
        # v6: a new category at a prefix where the base already has a valid message is an ANNOTATION, not guidance
        spec = [r for r in new if pk(r["t"], r["phase"]) not in bpre]
        specific = _brief(spec[0]) if (spec and cls != "ADDITIONAL") else None
        annotation = _brief(new[0]) if (new and not spec and cls != "ADDITIONAL") else None
        tv = {pk(r["t"], r["phase"]) for r in top_rows if tuple(r["identity"]) == idn and r["verdict"] == "VALID"
              and not r["contradicted"] and r["kind"] not in ("NOTE", "POSITIVE")}
        out.append({"identity": list(idn), "class": cls, "more_actionable": actionable, "more_specific": specific,
                    "same_prefix_annotation": annotation,
                    "base": _brief(bf) if bf else None, "top": _brief(xf),
                    "top_redundant_repeats": max(0, len(tv) - 1), "top_invalid": x["n_invalid"],
                    "base_invalid": b["n_invalid"] if b else 0,
                    "top_first_any_invalid_before_valid": (x["first_any"] is not x["first_valid"])})
    return out


def _brief(r):
    return {k: r[k] for k in ("key", "cat", "kind", "tier", "layer", "t", "phase", "verdict", "why")}


def summary(rows):
    s = {"occurrences": len(rows), "by_kind": {}, "by_verdict": {}, "invalid_by_cat": {}, "contradicted": 0}
    for r in rows:
        s["by_kind"][r["kind"]] = s["by_kind"].get(r["kind"], 0) + 1
        s["by_verdict"][r["verdict"]] = s["by_verdict"].get(r["verdict"], 0) + 1
        if r["verdict"] != "VALID":
            c = f"{r['cat']}|{r['verdict']}"
            s["invalid_by_cat"][c] = s["invalid_by_cat"].get(c, 0) + 1
        s["contradicted"] += int(r["contradicted"])
    return s


def lifecycle(rows, P, sp, type_of):
    """ADVICE problems: last valid occurrence and how the advice ended (followed / obsolete / suspended)"""
    out = []
    pr = problems(rows)
    for idn, p in pr.items():
        if p["first_valid"] is None or p["first_valid"]["kind"] != "ADVICE":
            continue
        obj = idn[1]
        last = max(r["t"] for r in rows if tuple(r["identity"]) == idn and r["verdict"] == "VALID"
                   and r["kind"] == "ADVICE")
        k, why = PV.advice_end(obj, last, P, sp, type_of)
        out.append({"identity": list(idn), "first_valid": p["first_valid"]["t"], "last_valid": last,
                    "n_valid": p["n_valid"], "end_step": k, "end": why})
    return out
