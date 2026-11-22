"""The spend ledger's over-grant: a deterministic reproduction, and the three guards that close it.

WHY THIS FILE EXISTS. `tests/test_officeqa_campaign_guards.py` races four real processes at a cap of 23
and asserts their total stays within it. That test FAILED once (24 against 23) and then passed ten times
in a row, which is the worst possible evidence: a race that usually passes tells you nothing about
whether it is fixed. These tests pin the MECHANISM instead, with no threads and no forks, so they fail
deterministically if the defect returns.

THE MECHANISM. A claim is created with O_CREAT|O_EXCL under a sequence number, and the old docstring
claimed that stopped two processes being granted the same block. It did not, because the file NAME
carried the amount:

    {seq:06d}-{role}-{resource}-{amount}.json

Two callers reaching one sequence number with DIFFERENT amounts wrote DIFFERENT names, so the exclusive
create never fired and both claims counted. They reached different amounts because
`give = min(block, cap - granted)` and `granted` was assembled from two separate directory listings -
claims, then releases - so a release landing between them lowered one caller's `granted` and not the
other's. Near the cap, one computed a partial block and the other a full one.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ledger():
    """Load the ledger by PATH. `import skillopt` drags in a dataclass(slots=) that needs 3.10."""
    spec = importlib.util.spec_from_file_location(
        "spend_ledger_race_test", os.path.join(REPO, "skillopt", "model", "spend_ledger.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


CAP, BLOCK = 23, 2          # exactly the parameters the failing run used


def _setup(tmp_path, monkeypatch, cap=CAP, block=BLOCK):
    d = str(tmp_path)
    for sub in ("claims", "spend", "release"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    for k, v in {
        "SKILLOPT_SPEND_LEDGER_DIR": d,
        "SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET": str(cap),
        "SKILLOPT_LEDGER_MAX_USD_TARGET": "100.0",
        "SKILLOPT_LEDGER_PRICE_TARGET": "0.04,0.07,0.02",
        "SKILLOPT_LEDGER_BLOCK_ATTEMPTS_TARGET": str(block),
        "SKILLOPT_LEDGER_BLOCK_USD_TARGET": "1.0",
        "SKILLOPT_LEDGER_RESERVE_USD_TARGET": "0.01",
        "SKILLOPT_LEDGER_MAX_ATTEMPTS_OPTIMIZER": "4",
        "SKILLOPT_LEDGER_MAX_USD_OPTIMIZER": "1.0",
        "SKILLOPT_LEDGER_PRICE_OPTIMIZER": "0.15,0.60,0.003",
    }.items():
        monkeypatch.setenv(k, v)
    return d


def _fill(d, n_blocks, amount=BLOCK):
    """n_blocks claims of `amount`, as a run that has nearly reached the cap really looks."""
    claims = os.path.join(d, "claims")
    for i in range(1, n_blocks + 1):
        with open(os.path.join(claims, "%06d-target-attempts-%d.json" % (i, amount)), "w") as f:
            json.dump({"schema": "lso.spend_ledger.v1", "amount": amount}, f)
    return sorted(os.listdir(claims))


# ── the reproduction ─────────────────────────────────────────────────────────

def test_a_stale_claims_view_with_a_fresh_release_view_cannot_over_grant(tmp_path, monkeypatch):
    """The exact interleaving that produced 24 against a cap of 23, as a single-threaded script.

    Eleven blocks of 2 are claimed (22 of 23). One ordinary grant takes the last unit. Then a second
    caller arrives whose CLAIMS listing is the snapshot from before that grant, while its RELEASE listing
    already shows a release of 1 - two ordinary events in an unlucky order. Under the old code it
    computed `granted = 22 - 1 = 21`, decided it had room for a full block, wrote a name that differed
    from the first claim's only in the amount, and the net grant became 24.

    Now it must not be granted anything. Failing closed is the correct outcome: a ledger whose view of
    shared storage is demonstrably behind cannot authorize a payment.
    """
    d = _setup(tmp_path, monkeypatch)
    L = _ledger()
    stale = _fill(d, 11)
    claims = os.path.join(d, "claims")

    got_a = L._grant("target", "attempts", BLOCK, CAP, note="A")
    assert got_a == 1, "with 22 of 23 claimed, an ordinary grant may have exactly the last unit"

    with open(os.path.join(d, "release", "000001-target-attempts-1.json"), "w") as f:
        json.dump({"schema": "lso.spend_ledger.v1", "kind": "release", "amount": 1}, f)

    real = os.listdir

    def staged(path):
        if os.path.abspath(path) == os.path.abspath(claims):
            return list(stale)          # the view grant A has already invalidated
        return real(path)

    os.listdir = staged                 # not monkeypatch: `undo` would also revert the environment
    try:
        with pytest.raises(L.LedgerUnusable) as exc:
            L._grant("target", "attempts", BLOCK, CAP, note="B")
    finally:
        os.listdir = real
    assert "cannot be trusted" in str(exc.value)
    assert "no request was sent" in str(exc.value)

    net = L._granted().get(("target", "attempts"), 0)
    assert net <= CAP, f"net granted {net} exceeds the cap of {CAP}"
    # 23 claimed (eleven blocks of 2, then grant A's last unit) minus the 1 an exiting process gave
    # back. Under the old code the same state read 24, which is one MORE than the cap rather than one
    # less: that single unit is the whole defect.
    assert net == 22


def test_the_sequence_token_carries_no_amount(tmp_path, monkeypatch):
    """The guard itself: exclusivity must not depend on two callers choosing the same amount."""
    d = _setup(tmp_path, monkeypatch)
    L = _ledger()
    _fill(d, 11)
    L._grant("target", "attempts", BLOCK, CAP, note="A")
    names = sorted(os.listdir(os.path.join(d, "claims")))
    tokens = [n for n in names if n.endswith(".seq")]
    assert tokens == ["000012-target-attempts.seq"], names
    assert L._SEQ_TOKEN.match(tokens[0])
    assert not L._CLAIM_NAME.match(tokens[0]), "a token must never be counted as a claim"


def test_releases_are_read_before_claims(tmp_path, monkeypatch):
    """A listing that is out of date must only ever OVERSTATE what has been granted.

    Reading claims first and releases second left the one dangerous direction: a release counted against
    a claim that the listing had not yet shown. The order is the fix, so it is asserted directly - the
    release listing happens first.
    """
    d = _setup(tmp_path, monkeypatch)
    L = _ledger()
    _fill(d, 5)
    order = []
    real = os.listdir

    def watched(path):
        order.append("release" if path.rstrip("/").endswith("release") else
                     "claims" if path.rstrip("/").endswith("claims") else "other")
        return real(path)

    os.listdir = watched
    try:
        L._granted()
    finally:
        os.listdir = real
    assert order[:2] == ["release", "claims"], order


def test_an_interrupted_grant_leaves_a_token_that_spends_nothing(tmp_path, monkeypatch):
    """A process that wins a sequence and dies before claiming. `_grant` returns only after the claim is
    written, so nothing was spent; the token must advance the sequence and be reported, not counted."""
    d = _setup(tmp_path, monkeypatch)
    L = _ledger()
    _fill(d, 2)
    claims = os.path.join(d, "claims")
    open(os.path.join(claims, "000003-target-attempts.seq"), "w").close()

    assert L._granted().get(("target", "attempts"), 0) == 4, "a token is not a claim"
    assert L.orphan_seq_tokens() == ["000003-target-attempts.seq"]
    # the next grant must step PAST the orphan, not collide with it for ever
    got = L._grant("target", "attempts", BLOCK, CAP, note="after an orphan")
    assert got == BLOCK
    assert os.path.exists(os.path.join(claims, "000004-target-attempts-2.json"))
    rep = L.report()
    assert rep["orphan_seq_tokens"] == ["000003-target-attempts.seq"]
    assert rep["verdict"] == "PASS", rep.get("problems")


def test_a_release_cannot_be_written_twice_at_one_sequence(tmp_path, monkeypatch):
    """`_write_release` had the identical defect, and in the more dangerous direction: two releases both
    subtracted lower `granted`, which is what lets the cap be exceeded."""
    d = _setup(tmp_path, monkeypatch)
    L = _ledger()
    rel = os.path.join(d, "release")
    L._write_release("target", "attempts", 3, "first")
    names = sorted(os.listdir(rel))
    assert "000001-target-attempts.seq" in names
    assert "000001-target-attempts-3.json" in names
    L._write_release("target", "attempts", 5, "second")
    names = sorted(os.listdir(rel))
    # the second release must take its OWN sequence number, not a second name at the first
    assert "000002-target-attempts-5.json" in names, names
    assert len([n for n in names if n.startswith("000001-") and n.endswith(".json")]) == 1, names


def test_the_grant_lock_is_exclusive_and_is_given_back(tmp_path, monkeypatch):
    d = _setup(tmp_path, monkeypatch)
    L = _ledger()
    monkeypatch.setenv("SKILLOPT_LEDGER_LOCK_WAIT_S", "0.2")
    held = L._acquire_grant_lock("test")
    assert os.path.exists(held)
    with pytest.raises(L.LedgerUnusable) as exc:
        L._acquire_grant_lock("test again")
    assert "No claim was made and no request was sent." in str(exc.value)
    L._release_grant_lock(held)
    assert not os.path.exists(held)
    again = L._acquire_grant_lock("after release")
    L._release_grant_lock(again)
    # and an ordinary grant leaves no lock behind
    L._grant("target", "attempts", BLOCK, CAP, note="clean")
    assert not os.path.exists(os.path.join(d, L._LOCK_NAME))


def test_a_stale_lock_is_broken_and_the_breaking_is_recorded(tmp_path, monkeypatch):
    d = _setup(tmp_path, monkeypatch)
    L = _ledger()
    monkeypatch.setenv("SKILLOPT_LEDGER_LOCK_WAIT_S", "1.0")
    monkeypatch.setenv("SKILLOPT_LEDGER_LOCK_STALE_S", "0")
    L._init("target")                      # so the record can be written
    orphan = os.path.join(d, L._LOCK_NAME)
    with open(orphan, "w") as f:
        json.dump({"owner": "a process that died"}, f)
    got = L._grant("target", "attempts", BLOCK, CAP, note="after a stale lock")
    assert got == BLOCK
    rows = []
    for name in os.listdir(os.path.join(d, "spend")):
        rows += [json.loads(l) for l in open(os.path.join(d, "spend", name)) if l.strip()]
    assert any(r.get("kind") == "lock_broken" for r in rows), "breaking a lock must be recorded"


def test_a_ledger_written_by_the_old_code_still_reads(tmp_path, monkeypatch):
    """The campaign's own ledger has no tokens in it. It must still read exactly as it did."""
    d = _setup(tmp_path, monkeypatch)
    L = _ledger()
    _fill(d, 4)
    with open(os.path.join(d, "release", "000001-target-attempts-3.json"), "w") as f:
        json.dump({"amount": 3}, f)
    assert L._granted().get(("target", "attempts"), 0) == 8 - 3
    assert L.orphan_seq_tokens() == []


def test_the_cap_is_still_reachable_to_its_last_unit(tmp_path, monkeypatch):
    """The fix must not make the tail of an authorization unreachable: that was the earlier failure the
    release mechanism was added for, where 45 per cent of a cap was claimed-but-unspendable."""
    d = _setup(tmp_path, monkeypatch)
    L = _ledger()
    total = 0
    while True:
        got = L._grant("target", "attempts", BLOCK, CAP, note="drain")
        if got <= 0:
            break
        total += got
    assert total == CAP, f"only {total} of a cap of {CAP} could be claimed"
    assert L._granted()[("target", "attempts")] == CAP
