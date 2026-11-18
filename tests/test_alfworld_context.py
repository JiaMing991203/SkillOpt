"""Rule AWC-1: the soundness traps the ALFWorld context-checking study was asked to cover.

EVERY INPUT IN THIS FILE IS CONSTRUCTED. These tests establish what the code does on hand-written
observation sentences; they are not evidence about any trace. The empirical results live in
`$RUNS/20261118-alfworld-context/` and are reported separately in
`docs/research/ALFWORLD_CONTEXT_PACKET.md`.

The two readings under test are both EXISTING code, snapshotted read-only under
`research/alfworld_context/frozen/`:
  CURRENT        `stage_common.Reading`          - the frozen shared reading (INSPECT clause a only)
  CONTEXT-AWARE  `a4_reading.ReadingA4(rev=2)`   - amendment A4 3.4's revision (clauses b, c, d)
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FROZEN = os.path.join(REPO, "research", "alfworld_context", "frozen", "learned_contracts")


def _mod(name):
    if FROZEN not in sys.path:
        sys.path.insert(0, FROZEN)
    spec = importlib.util.spec_from_file_location(f"{name}_awc_test", os.path.join(FROZEN, f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SC = _mod("stage_common")
A4 = _mod("a4_reading")
V = _mod("vocab")


def steps(*pairs):
    """[(action, observation)] -> the analyst-visible step list through the frozen grammar."""
    return A4.parse_steps([{"action": a, "obs": o} for a, o in pairs],
                          V.parse_command, V.parse_obs, V.outcome_of)


def read(goal, pairs, rev=2, current=False):
    sp = SC.spec_of(goal)
    vis = steps(*pairs)
    R = SC.Reading(sp, V.type_of) if current else A4.ReadingA4(sp, V.type_of, rev)
    for s in vis:
        R.apply(s)
    return R


SIMPLE = {"form": "simple", "object_type": "cup", "target_type": "cabinet"}
TWO = {"form": "two", "object_type": "cup", "target_type": "cabinet"}
LIGHT = {"form": "light", "object_type": "tissuebox", "target_type": "desklamp"}
HEAT = {"form": "heat", "object_type": "apple", "target_type": "sidetable"}

GO = lambda r: (f"go to {r}", f"You arrive at {r}. On the {r}, you see nothing.")      # noqa: E731
TAKE = lambda o, r: (f"take {o} from {r}", f"You pick up the {o} from the {r}.")        # noqa: E731
PUT = lambda o, r: (f"move {o} to {r}", f"You move the {o} to the {r}.")                # noqa: E731
USE = lambda l: (f"use {l}", f"You turn on the {l}.")                                   # noqa: E731


# ───────────────────────── 1. a supported fact survives irrelevant events ─────────────────────────

def test_a_placement_survives_irrelevant_events():
    R = read(SIMPLE, [GO("countertop 1"), TAKE("cup 1", "countertop 1"), GO("cabinet 3"),
                      PUT("cup 1", "cabinet 3"),
                      GO("sinkbasin 1"), ("look", "You are facing the sinkbasin 1."),
                      GO("microwave 1"), GO("shelf 2")])
    assert R.supported() == ["cup 1"]
    assert R.placed["cup 1"]["recep"] == "cabinet 3"
    assert R.goal_now() == ["cup 1"], "four irrelevant steps later it is still supported"


# ───────────────────────── 2. invalidation, and a conflicting observation ─────────────────────────

def test_an_observed_pick_up_withdraws_the_placement():
    R = read(SIMPLE, [TAKE("cup 1", "countertop 1"), PUT("cup 1", "cabinet 3"),
                      TAKE("cup 1", "cabinet 3")])
    assert R.supported() == [], "the placement is withdrawn, not merely stale"
    assert ("withdrawn" in [e[3] for e in R.pevents])


def test_a_listing_that_omits_the_object_is_a_conflict_not_a_silent_drop():
    R = read(SIMPLE, [TAKE("cup 1", "countertop 1"), PUT("cup 1", "cabinet 3"),
                      ("go to cabinet 3", "You arrive at cabinet 3. On the cabinet 3, you see a mug 2.")])
    assert R.placed["cup 1"]["basis"] == "conflict"
    assert R.supported() == [], "a conflicting placement is not counted"
    assert ("conflict" in [e[3] for e in R.pevents]), "and the conflict is recorded as an event"


def test_a_listing_that_names_the_object_confirms_the_placement():
    R = read(SIMPLE, [TAKE("cup 1", "countertop 1"), PUT("cup 1", "cabinet 3"),
                      ("go to cabinet 3", "You arrive at cabinet 3. On the cabinet 3, you see a cup 1.")])
    assert R.placed["cup 1"]["basis"] == "confirmed"
    assert R.placed["cup 1"]["confirmed"] == [2]


# ───────────────────────── 3. two instances of one type are two facts ─────────────────────────

def test_a_fact_about_one_instance_does_not_transfer_to_another_of_the_same_type():
    R = read(SIMPLE, [TAKE("cup 1", "countertop 1"), PUT("cup 1", "cabinet 3"),
                      ("go to cabinet 3", "You arrive at cabinet 3. On the cabinet 3, you see a cup 2.")])
    assert R.placed["cup 1"]["basis"] == "conflict", "cup 2 does not confirm cup 1"
    assert "cup 2" not in R.placed, "and seeing cup 2 there does not place it"


def test_the_lamp_fact_is_tied_to_the_object_that_was_held():
    R = read(LIGHT, [TAKE("tissuebox 1", "desk 1"), USE("desklamp 1")])
    assert R.goal_now() == ["tissuebox 1"]
    R2 = read(LIGHT, [TAKE("tissuebox 2", "desk 1"), USE("desklamp 1")])
    assert R2.goal_now() == ["tissuebox 2"], "the other instance, and only it"


# ───────────────────────── 4. alternate valid action orders ─────────────────────────

def test_both_orders_discharge_the_lamp_stage_under_the_context_reading():
    held_first = [GO("desk 1"), TAKE("tissuebox 1", "desk 1"), USE("desklamp 1")]
    lamp_first = [GO("desk 1"), USE("desklamp 1"), TAKE("tissuebox 1", "desk 1")]
    assert read(LIGHT, held_first).goal_now() == ["tissuebox 1"]
    assert read(LIGHT, lamp_first).goal_now() == ["tissuebox 1"]
    # and the basis differs, which is the point of recording it
    assert read(LIGHT, held_first).inspect_basis == "a"
    assert read(LIGHT, lamp_first).inspect_basis == "b"


def test_the_current_reading_discharges_only_the_coincident_order():
    held_first = [GO("desk 1"), TAKE("tissuebox 1", "desk 1"), USE("desklamp 1")]
    lamp_first = [GO("desk 1"), USE("desklamp 1"), TAKE("tissuebox 1", "desk 1")]
    assert read(LIGHT, held_first, current=True).goal_now() == ["tissuebox 1"]
    assert read(LIGHT, lamp_first, current=True).goal_now() == [], \
        "the pre-revision reading needs the lamp use WHILE the object is held"


# ───────────────────────── 5. an incomplete observation stays unknown ─────────────────────────

def test_an_object_never_observed_is_not_available_and_is_not_declared_absent():
    R = read(SIMPLE, [("go to cabinet 3", "You arrive at cabinet 3. On the cabinet 3, you see a mug 2.")])
    assert R.available() == [], "nothing of the requested type is known to be anywhere"
    assert R.supported() == []
    assert "cup 1" not in R.seen, "and nothing asserts that cup 1 does not exist"
    assert "listing_complete" in SC.EXTENDED_ASSUMPTIONS, \
        "listing completeness is an EXTENDED assumption, never used by a primary finding"


def test_a_failed_command_changes_nothing():
    base = [TAKE("cup 1", "countertop 1"), PUT("cup 1", "cabinet 3")]
    R = read(SIMPLE, base)
    R2 = read(SIMPLE, base + [("take cup 1", "Nothing happens.")])
    assert R2.supported() == R.supported() == ["cup 1"]
    assert R2.placed["cup 1"] == R.placed["cup 1"], "an observed failure updates nothing"


def test_an_unmodelled_action_leaves_the_state_alone():
    base = [TAKE("tissuebox 1", "desk 1")]
    R = read(LIGHT, base + [("examine tissuebox 1", "There's nothing special about tissuebox 1.")])
    assert R.goal_now() == [], "examine is not a lamp use, so nothing is discharged"
    assert sorted(R.held) == ["tissuebox 1"], "and nothing it did know is lost"


# ───────────────────────── 6. recovery after a detour ─────────────────────────

def test_a_second_placement_restores_support_after_a_detour():
    R = read(SIMPLE, [TAKE("cup 1", "countertop 1"), PUT("cup 1", "cabinet 3"),
                      TAKE("cup 1", "cabinet 3"), GO("sinkbasin 1"), GO("microwave 1"),
                      GO("cabinet 3"), PUT("cup 1", "cabinet 3")])
    assert R.supported() == ["cup 1"]
    assert [e[3] for e in R.pevents] == ["placed", "withdrawn", "restored"]


# ───────────────────────── 7. the lamp traps the instruction names ─────────────────────────

def test_the_lamp_connection_needs_the_object_at_the_lamp_s_place():
    """`same_place`: the lamp was turned on at dresser 1 and the object was picked up at desk 1."""
    pairs = [GO("dresser 1"), USE("desklamp 1"), GO("desk 1"), TAKE("tissuebox 1", "desk 1")]
    assert read(LIGHT, pairs).goal_now() == [], \
        "a lamp-on elsewhere does not support the lighting stage"


def test_an_arrival_in_between_breaks_the_lamp_connection():
    pairs = [GO("dresser 1"), USE("desklamp 1"), GO("desk 1"), GO("dresser 1"),
             TAKE("tissuebox 1", "dresser 1")]
    assert read(LIGHT, pairs).goal_now() == [], \
        "the pick-up must happen at the arrival the lamp was turned on at, with none in between"


def test_the_persistence_rule_is_named_and_justified_rather_than_assumed():
    assert set(A4.INSPECT_ASSUMPTIONS) == {"lamp_stays_on", "same_place"}
    assert "no recorded command turns a lamp off" in A4.INSPECT_ASSUMPTIONS["lamp_stays_on"]
    assert "use" not in V.VERBS or "turn_off" not in str(V.VERBS.get("use")), \
        "there is no switch-off command in the vocabulary, which is why the rule is admissible"


def test_a_lamp_on_is_a_recorded_event_not_a_state():
    R = read(LIGHT, [GO("dresser 1"), USE("desklamp 1")])
    assert R.lamp_on and R.lamp_on[0][0] == 1, "the record is (step, lamp, ...)"
    assert R.goal_now() == [], "nothing is discharged while no requested object is held"
    assert not hasattr(R, "lamp_is_on"), "no 'the lamp is on now' property exists"


# ───────────────────────── 8. the rev=1 defect, recorded as a test ─────────────────────────

def test_rev1_does_not_reproduce_the_pre_revision_lamp_reading():
    """`a4_reading`'s docstring says rev=1 "reproduces the reading used for Study A's frozen reports".
    For `look_at_obj_in_light` it does not: `goal_now` gates clause (c) on `self.rev` and applies the
    INSPECT clause (b) unconditionally. This test records the behaviour that is actually in force, so a
    later change to it is deliberate rather than silent."""
    pairs = [GO("desk 1"), USE("desklamp 1"), TAKE("tissuebox 1", "desk 1")]
    assert read(LIGHT, pairs, rev=1).goal_now() == ["tissuebox 1"], "clause (b) fires at rev 1 too"
    assert read(LIGHT, pairs, current=True).goal_now() == [], "the shared reading is the real baseline"


# ───────────────────────── 9. two-object identity (clause c) ─────────────────────────

def test_two_instances_in_different_receptacles_of_the_right_type():
    """The tr:2986 pattern, constructed: 'cabinet' as a TYPE is not 'cabinet 6' as an INSTANCE."""
    pairs = [TAKE("cup 1", "countertop 1"), PUT("cup 1", "cabinet 3"),
             TAKE("cup 4", "countertop 2"), PUT("cup 4", "cabinet 6")]
    assert read(TWO, pairs, current=True).goal_now() == ["cup 1", "cup 4"], \
        "the current reading counts two supported placements in cabinets"
    assert read(TWO, pairs).goal_now() == ["cup 1"], \
        "the context reading requires one cabinet INSTANCE and reports a single instance there"


def test_two_instances_in_one_receptacle_instance_discharge_the_stage():
    pairs = [TAKE("cup 1", "countertop 1"), PUT("cup 1", "cabinet 3"),
             TAKE("cup 4", "countertop 2"), PUT("cup 4", "cabinet 3")]
    assert read(TWO, pairs).goal_now() == ["cup 1", "cup 4"]


# ───────────────────────── 10. a transformation can be superseded (clause d) ─────────────────────────

def test_a_later_opposite_transformation_supersedes_the_requested_one():
    pairs = [TAKE("apple 1", "countertop 1"),
             ("heat apple 1 with microwave 1", "You heat the apple 1 using the microwave 1."),
             ("cool apple 1 with fridge 1", "You cool the apple 1 using the fridge 1."),
             PUT("apple 1", "sidetable 1")]
    assert read(HEAT, pairs, current=True).goal_now() == ["apple 1"], \
        "the current reading only asks whether a heat was recorded before the placement"
    assert read(HEAT, pairs).goal_now() == [], \
        "the context reading asks whether the heat is the LATEST temperature event"


def test_the_requested_transformation_as_the_latest_one_discharges():
    pairs = [TAKE("apple 1", "countertop 1"),
             ("cool apple 1 with fridge 1", "You cool the apple 1 using the fridge 1."),
             ("heat apple 1 with microwave 1", "You heat the apple 1 using the microwave 1."),
             PUT("apple 1", "sidetable 1")]
    assert read(HEAT, pairs).goal_now() == ["apple 1"]


def test_a_transformation_is_a_recorded_event_not_a_property():
    R = read(HEAT, [TAKE("apple 1", "countertop 1"),
                    ("heat apple 1 with microwave 1", "You heat the apple 1 using the microwave 1.")])
    assert R.transforms["apple 1"] == [(1, "heat", "microwave 1")]
    assert not hasattr(R, "temperature"), "no temperature property is ever asserted"


# ───────────────────────── the frozen snapshot must still be the code under study ─────────────────

def test_the_frozen_snapshot_matches_what_it_claims():
    sys.path.insert(0, os.path.join(REPO, "research", "alfworld_context"))
    spec = importlib.util.spec_from_file_location(
        "awc_manifest_test", os.path.join(REPO, "research", "alfworld_context", "awc_manifest.py"))
    M = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(M)
    d = M.verify_snapshot()
    assert all(r["snapshot_intact"] for r in d["files"]), d["files"]


@pytest.mark.parametrize("fn", ["goal_now", "apply", "supported", "available"])
def test_the_shared_reading_still_has_the_methods_the_study_compares(fn):
    assert callable(getattr(SC.Reading, fn))
