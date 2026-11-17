"""The two guards the three-arm campaign depends on, tested against the behaviour they promise.

1. ``skillopt.model.spend_ledger`` - the attempt and spend caps must hold ACROSS CONCURRENT PROCESSES and
   ACROSS RESTARTS, which is what the campaign needs and what a per-process counter cannot give. The
   concurrency test really forks processes and races them at one cap; the restart test really starts a fresh
   interpreter with the same ledger directory.

2. ``skillopt.optimizer.prompt_budget`` - the pre-send size check, its fixed drop order (rule OQA-PB-1), and
   the guarantee that both guards are INERT when their environment variables are unset, so an arm that does not
   set them runs upstream's code path.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import subprocess
import sys
import textwrap

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from skillopt.model import spend_ledger as LEDGER  # noqa: E402
from skillopt.optimizer import prompt_budget as PB  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_env_leak():
    """These tests set os.environ directly (a child process must inherit it), so restore it afterwards.

    Without this the ledger directory leaks into every later test in the session and
    tests/test_openai_compatible_*.py start failing because the backend reserves against a stale ledger.
    """
    before = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)
        LEDGER._state.clear()
        LEDGER._owner = ""


def _env(tmpdir: str, *, attempts: int, usd: float, block_attempts: int = 2,
         block_usd: float = 1.0, reserve: float = 0.01) -> dict:
    return {
        "SKILLOPT_SPEND_LEDGER_DIR": tmpdir,
        "SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET": str(attempts),
        "SKILLOPT_LEDGER_MAX_USD_TARGET": str(usd),
        "SKILLOPT_LEDGER_PRICE_TARGET": "0.04,0.07,0.02",
        "SKILLOPT_LEDGER_BLOCK_ATTEMPTS_TARGET": str(block_attempts),
        "SKILLOPT_LEDGER_BLOCK_USD_TARGET": str(block_usd),
        "SKILLOPT_LEDGER_RESERVE_USD_TARGET": str(reserve),
        "SKILLOPT_LEDGER_MAX_ATTEMPTS_OPTIMIZER": "4",
        "SKILLOPT_LEDGER_MAX_USD_OPTIMIZER": "1.0",
        "SKILLOPT_LEDGER_PRICE_OPTIMIZER": "0.15,0.60,0.003",
    }


def _apply(env: dict, monkeypatch) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    LEDGER._state.clear()
    LEDGER._owner = ""


def _spend_until_refused(limit: int = 200) -> int:
    used = 0
    for _ in range(limit):
        try:
            LEDGER.reserve("target", "test")
        except LEDGER.BudgetExhausted:
            return used
        used += 1
    raise AssertionError("the ledger never refused")


# ── the ledger: inert by default ──────────────────────────────────────────────

def test_the_ledger_is_inert_when_its_directory_is_not_set(monkeypatch):
    monkeypatch.delenv("SKILLOPT_SPEND_LEDGER_DIR", raising=False)
    LEDGER._state.clear()
    assert LEDGER.active() is False
    for _ in range(1000):
        LEDGER.reserve("target", "test")       # no cap, no file, no exception
    assert LEDGER.record_usage("target", "test", 10_000, 1_000) == 0.0
    assert LEDGER.snapshot() == {"active": False}


# ── the ledger: one process ───────────────────────────────────────────────────

def test_the_attempt_cap_stops_a_single_process_exactly_at_the_cap(tmp_path, monkeypatch):
    _apply(_env(str(tmp_path), attempts=7, usd=10.0), monkeypatch)
    assert _spend_until_refused() == 7
    report = LEDGER.report()
    assert report["verdict"] == "PASS"
    assert report["observed"]["by_role"]["target"]["attempts"] == 7
    assert report["observed"]["by_role"]["target"]["refusals"] == 1


def test_the_usd_cap_stops_a_process_before_it_overspends(tmp_path, monkeypatch):
    # $0.10 of authorization, $0.02 of headroom required per attempt -> 5 attempts' worth of claims.
    _apply(_env(str(tmp_path), attempts=10_000, usd=0.10, block_usd=0.02, reserve=0.02), monkeypatch)
    used = 0
    while True:
        try:
            LEDGER.reserve("target", "test")
        except LEDGER.BudgetExhausted:
            break
        used += 1
        LEDGER.record_usage("target", "test", 500_000, 0)   # $0.02 exactly at $0.04/M
        if used > 50:
            raise AssertionError("the usd cap never bound")
    assert used == 5
    assert LEDGER.observed()["by_role"]["target"]["usd"] == pytest.approx(0.10)
    assert LEDGER.report()["verdict"] == "PASS"


def test_the_charge_is_computed_from_the_configured_prices(tmp_path, monkeypatch):
    _apply(_env(str(tmp_path), attempts=10, usd=10.0), monkeypatch)
    # 1,000,000 prompt of which 250,000 cached, 100,000 completion
    usd = LEDGER.cost_usd("target", 1_000_000, 100_000, 250_000)
    assert usd == pytest.approx(750_000 * 0.04 / 1e6 + 250_000 * 0.02 / 1e6 + 100_000 * 0.07 / 1e6)


# ── the ledger: fail closed ───────────────────────────────────────────────────

def test_a_usd_cap_without_prices_refuses_to_let_anything_be_sent(tmp_path, monkeypatch):
    env = _env(str(tmp_path), attempts=10, usd=1.0)
    env.pop("SKILLOPT_LEDGER_PRICE_TARGET")
    _apply(env, monkeypatch)
    monkeypatch.delenv("SKILLOPT_LEDGER_PRICE_TARGET", raising=False)
    with pytest.raises(LEDGER.LedgerUnusable):
        LEDGER.reserve("target", "test")


def test_a_missing_cap_refuses_to_let_anything_be_sent(tmp_path, monkeypatch):
    env = _env(str(tmp_path), attempts=10, usd=1.0)
    env.pop("SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET")
    _apply(env, monkeypatch)
    monkeypatch.delenv("SKILLOPT_LEDGER_MAX_ATTEMPTS_TARGET", raising=False)
    with pytest.raises(LEDGER.LedgerUnusable):
        LEDGER.reserve("target", "test")


def test_a_ledger_directory_that_cannot_be_created_refuses_to_let_anything_be_sent(tmp_path, monkeypatch):
    """Portable: the ledger path's parent is a regular file, so makedirs fails on every filesystem."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a regular file\n")
    _apply(_env(str(blocker / "ledger"), attempts=10, usd=1.0), monkeypatch)
    with pytest.raises(LEDGER.LedgerUnusable):
        LEDGER.reserve("target", "test")


def test_an_unwritable_ledger_directory_refuses_to_let_anything_be_sent(tmp_path, monkeypatch):
    """The chmod form of the same property - skipped where the filesystem does not enforce chmod for the owner.

    /nfs/turbo, where the campaign's ledger lives, does NOT: job 66272358 chmod-ed a claim directory to 0500
    and then created a file in it, because the share's POSIX ACL mask grants the owner write access whatever
    the mode bits say. That is a limitation of this test, not of the ledger, and the test above covers the
    property portably.
    """
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    claims = blocked / "claims"
    claims.mkdir()
    os.chmod(claims, 0o500)
    try:
        probe = claims / ".probe"
        try:
            probe.write_text("x")
        except OSError:
            pass
        else:
            probe.unlink()
            pytest.skip("this filesystem does not enforce chmod 0500 against the owner")
        _apply(_env(str(blocked), attempts=10, usd=1.0), monkeypatch)
        with pytest.raises(LEDGER.LedgerUnusable):
            LEDGER.reserve("target", "test")
    finally:
        os.chmod(claims, 0o700)


# ── the ledger: concurrency ───────────────────────────────────────────────────

def _child(ledger_dir: str, cap: int, queue) -> None:
    os.environ.update(_env(ledger_dir, attempts=cap, usd=100.0, block_attempts=2))
    LEDGER._state.clear()
    LEDGER._owner = ""
    spent = 0
    for _ in range(500):
        try:
            LEDGER.reserve("target", "concurrent")
        except LEDGER.BudgetExhausted:
            break
        spent += 1
    queue.put(spent)


def test_four_concurrent_processes_cannot_spend_more_than_the_one_shared_cap(tmp_path):
    """The campaign runs up to four jobs at once against one account-wide authorization."""
    ledger_dir = str(tmp_path)
    os.makedirs(os.path.join(ledger_dir, "claims"), exist_ok=True)
    os.makedirs(os.path.join(ledger_dir, "spend"), exist_ok=True)
    cap = 23          # deliberately not a multiple of the block size
    ctx = mp.get_context("fork")
    queue = ctx.Queue()
    procs = [ctx.Process(target=_child, args=(ledger_dir, cap, queue)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
        assert p.exitcode == 0, f"child failed with exit code {p.exitcode}"
    totals = [queue.get() for _ in procs]
    assert sum(totals) <= cap, f"four processes spent {sum(totals)} against a cap of {cap}: {totals}"
    assert sum(totals) > 0
    # And the record on disk agrees with what the children reported.
    os.environ.update(_env(ledger_dir, attempts=cap, usd=100.0))
    LEDGER._state.clear()
    observed = LEDGER.observed()["by_role"]["target"]["attempts"]
    assert observed == sum(totals)
    assert observed <= cap


# ── the ledger: restarts ──────────────────────────────────────────────────────

_RESTART_CHILD = textwrap.dedent(
    """
    import json, os, sys
    sys.path.insert(0, %(repo)r)
    from skillopt.model import spend_ledger as L
    spent = 0
    for _ in range(500):
        try:
            L.reserve("target", "restart")
        except L.BudgetExhausted:
            break
        spent += 1
    print(json.dumps({"spent": spent}))
    """
)


def test_a_restarted_process_does_not_get_the_authorization_again(tmp_path):
    """A Slurm requeue starts a fresh process. The per-process counter this replaces reset on every restart."""
    ledger_dir = str(tmp_path)
    env = dict(os.environ)
    env.update(_env(ledger_dir, attempts=9, usd=100.0, block_attempts=3))
    code = _RESTART_CHILD % {"repo": REPO}

    first = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={**env, "SLURM_JOB_ID": "1", "SLURM_RESTART_COUNT": "0"})
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout.strip().splitlines()[-1])["spent"] == 9

    # Same ledger directory, a new process, a Slurm restart count of 1: nothing left.
    second = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            env={**env, "SLURM_JOB_ID": "1", "SLURM_RESTART_COUNT": "1"})
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout.strip().splitlines()[-1])["spent"] == 0

    os.environ.update(_env(ledger_dir, attempts=9, usd=100.0))
    LEDGER._state.clear()
    report = LEDGER.report()
    assert report["observed"]["by_role"]["target"]["attempts"] == 9
    assert report["verdict"] == "PASS"
    assert len(report["observed"]["by_owner"]) == 2, "the two processes should be recorded separately"


# ── the ledger: it is the one chokepoint the backend uses ─────────────────────

def test_the_openai_compatible_backend_reserves_before_it_sends(tmp_path, monkeypatch):
    """Every paid attempt in this campaign goes through `_chat_messages_impl`, so the hook must be there."""
    import skillopt.model.openai_compatible_backend as B

    _apply(_env(str(tmp_path), attempts=2, usd=100.0, block_attempts=1), monkeypatch)
    sent = []

    class _Msg:
        content = "ok"
        reasoning_content = None

    class _Choice:
        message = _Msg()
        finish_reason = "stop"

    class _Resp:
        choices = [_Choice()]
        usage = None
        id = "x"
        model = "m"

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    sent.append(kwargs)
                    return _Resp()

    monkeypatch.setattr(B, "_get_client", lambda role: _Client())
    monkeypatch.setattr(B, "_config_for", lambda role: B.OpenAICompatibleConfig(
        base_url="http://x", api_key="k", deployment="m", timeout_seconds=1.0,
        max_tokens=16, temperature=None))

    for _ in range(2):
        B._chat_messages_impl([{"role": "user", "content": "hi"}], 16, 1, "unit", role="target")
    assert len(sent) == 2
    with pytest.raises(LEDGER.BudgetExhausted):
        B._chat_messages_impl([{"role": "user", "content": "hi"}], 16, 1, "unit", role="target")
    assert len(sent) == 2, "the third attempt must not have reached the client"


def test_a_refusal_does_not_consume_the_retry_loop(tmp_path, monkeypatch):
    """`reserve` is called outside the try, so BudgetExhausted propagates instead of being retried."""
    import skillopt.model.openai_compatible_backend as B

    _apply(_env(str(tmp_path), attempts=0, usd=100.0, block_attempts=1), monkeypatch)
    calls = []
    monkeypatch.setattr(B, "_get_client", lambda role: calls.append(1))
    monkeypatch.setattr(B, "_config_for", lambda role: B.OpenAICompatibleConfig(
        base_url="http://x", api_key="k", deployment="m", timeout_seconds=1.0,
        max_tokens=16, temperature=None))
    with pytest.raises(LEDGER.BudgetExhausted):
        B._chat_messages_impl([{"role": "user", "content": "hi"}], 16, 5, "unit", role="target")


# ── the prompt budget ─────────────────────────────────────────────────────────

def _pairs(spec: list[tuple[str, str, int]]) -> list[dict]:
    return [{"id": i, "category": c, "prev": {"hard": 0, "soft": 0.0}, "curr": {"hard": 0, "soft": 0.0},
             "task": "q", "prev_trajectory": "x" * n, "curr_trajectory": "y" * n} for i, c, n in spec]


def _build(pairs: list[dict]) -> str:
    return "HEAD\n" + "".join(p["prev_trajectory"] + p["curr_trajectory"] for p in pairs)


def test_the_prompt_budget_is_inert_when_no_limit_is_set(monkeypatch):
    monkeypatch.delenv("SKILLOPT_OPTIMIZER_PROMPT_TOKEN_LIMIT", raising=False)
    pairs = _pairs([("a", "regressed", 10_000_000)])
    user, kept, rec = PB.fit("sys", pairs, _build, stage="slow_update")
    assert PB.active() is False
    assert kept == pairs and rec["skip"] is False and rec["checked"] is False
    assert "estimated_tokens_offered" not in rec


def test_a_prompt_inside_the_limit_is_sent_whole(monkeypatch):
    monkeypatch.setenv("SKILLOPT_OPTIMIZER_PROMPT_TOKEN_LIMIT", "225000")
    pairs = _pairs([(f"u{i}", "persistent_fail", 1_000) for i in range(10)])
    _user, kept, rec = PB.fit("sys", pairs, _build, stage="slow_update")
    assert len(kept) == 10 and rec["dropped"] == [] and rec["within_limit"] is True


def test_an_oversized_prompt_drops_pairs_in_the_fixed_order_until_it_fits(monkeypatch):
    monkeypatch.setenv("SKILLOPT_OPTIMIZER_PROMPT_TOKEN_LIMIT", "100000")
    # 320,000 chars per pair ~ 100,000 tokens each, so exactly one pair can be sent.
    pairs = _pairs([("keep", "regressed", 160_000), ("p", "persistent_fail", 160_000),
                    ("i", "improved", 160_000), ("s", "stable_success", 160_000)])
    _user, kept, rec = PB.fit("sys", pairs, _build, stage="slow_update")
    assert [p["id"] for p in kept] == ["keep"], "the regression must be the last pair standing"
    assert [d["id"] for d in rec["dropped"]] == ["s", "i", "p"]
    assert rec["pairs_sent"] == 1 and rec["pairs_offered"] == 4


def test_within_a_category_the_later_sampled_pair_is_dropped_first(monkeypatch):
    monkeypatch.setenv("SKILLOPT_OPTIMIZER_PROMPT_TOKEN_LIMIT", "100000")
    pairs = _pairs([("first", "persistent_fail", 160_000), ("second", "persistent_fail", 160_000)])
    _user, kept, rec = PB.fit("sys", pairs, _build, stage="slow_update")
    assert [p["id"] for p in kept] == ["first"]
    assert [d["id"] for d in rec["dropped"]] == ["second"]


def test_no_call_is_made_when_even_one_pair_is_over_the_limit(monkeypatch):
    monkeypatch.setenv("SKILLOPT_OPTIMIZER_PROMPT_TOKEN_LIMIT", "1000")
    pairs = _pairs([("a", "regressed", 1_000_000)])
    _user, kept, rec = PB.fit("sys", pairs, _build, stage="slow_update")
    assert rec["skip"] is True and len(kept) == 1
    assert "no smaller non-empty subset" in rec["skip_reason"]


def test_the_estimator_is_calibrated_on_the_proven_measurement():
    """3.20 chars/token must OVERSTATE the proven 243,986 tokens, never understate it."""
    estimate = PB.estimate_tokens("x" * PB.PROVEN["rendered_chars"])
    assert estimate >= PB.PROVEN["prompt_tokens"]
    assert estimate <= PB.PROVEN["prompt_tokens"] * 1.02


def test_the_limit_leaves_room_for_the_whole_completion_allowance():
    """225,000 + 16,384 must stay under the total DeepSeek demonstrably processed in job 66271290."""
    proven_total = PB.PROVEN["prompt_tokens"] + PB.PROVEN["completion_tokens"]
    assert 225_000 + 16_384 <= proven_total


def test_the_log_records_the_pair_count_and_whether_guidance_was_produced(tmp_path, monkeypatch):
    log = tmp_path / "prompt_budget.jsonl"
    monkeypatch.setenv("SKILLOPT_OPTIMIZER_PROMPT_TOKEN_LIMIT", "100000")
    monkeypatch.setenv("SKILLOPT_OPTIMIZER_PROMPT_BUDGET_LOG", str(log))
    monkeypatch.setenv("SKILLOPT_ARM_LABEL", "C")
    pairs = _pairs([("keep", "regressed", 160_000), ("s", "stable_success", 160_000)])
    _user, _kept, rec = PB.fit("sys", pairs, _build, stage="slow_update")
    PB.log(rec, guidance_produced=True)
    entry = json.loads(log.read_text().strip())
    assert entry["arm"] == "C" and entry["stage"] == "slow_update"
    assert entry["pairs_offered"] == 2 and entry["pairs_sent"] == 1
    assert entry["guidance_produced"] is True and entry["rule"] == "OQA-PB-1"


def test_the_same_rule_object_is_used_by_both_optimizer_stages():
    """The user's condition: one rule, applied to the slow update and the meta skill alike."""
    import skillopt.optimizer.meta_skill as MS
    import skillopt.optimizer.slow_update as SU
    assert MS.prompt_budget is PB and SU.prompt_budget is PB


# ── the route guard ───────────────────────────────────────────────────────────

def _pin(monkeypatch, *, optimizer=True, target=True):
    from skillopt.model import configure_openai_compatible, set_optimizer_backend, set_target_backend
    import skillopt.model.openai_compatible_backend as B
    if optimizer:
        monkeypatch.setenv("OPTIMIZER_OPENAI_COMPATIBLE_API_KEY", "k" * 35)
        set_optimizer_backend("openai_compatible")
    if target:
        monkeypatch.setenv("TARGET_OPENAI_COMPATIBLE_API_KEY", "t" * 40)
        set_target_backend("openai_compatible")
    configure_openai_compatible(
        optimizer_base_url="https://api.deepseek.com", optimizer_api_key="k" * 35,
        optimizer_model="deepseek-flash", optimizer_max_tokens=16384,
        target_base_url="https://api.empiriolabs.ai/v1", target_api_key="t" * 40,
        target_model="qwen3-5-4b", target_max_tokens=8000)
    B._reset_clients()


DEEPSEEK = "optimizer=openai_compatible|https://api.deepseek.com|deepseek-flash"
EMPIRIO = "target=openai_compatible|https://api.empiriolabs.ai/v1|qwen3-5-4b"


def test_the_route_guard_is_off_when_no_route_is_required(monkeypatch):
    from skillopt.model import route_guard as RG
    monkeypatch.delenv("SKILLOPT_REQUIRE_ROUTE", raising=False)
    assert RG.verify() == {"required": False}


def test_the_route_guard_passes_when_both_roles_resolve_as_required(tmp_path, monkeypatch):
    from skillopt.model import route_guard as RG
    _pin(monkeypatch)
    monkeypatch.setenv("SKILLOPT_REQUIRE_ROUTE", f"{EMPIRIO};{DEEPSEEK}")
    monkeypatch.setenv("SKILLOPT_ROUTE_GUARD_RECORD", str(tmp_path / "route.json"))
    monkeypatch.delenv("SKILLOPT_SPEND_LEDGER_DIR", raising=False)
    record = RG.verify()
    assert record["verdict"] == "PASS"
    assert record["roles"]["optimizer"]["base_url"] == "https://api.deepseek.com"
    assert record["roles"]["target"]["model"] == "qwen3-5-4b"
    for role in ("target", "optimizer"):
        assert "reaches the openai-compatible backend" in record["roles"][role]["dispatch_check"]
    written = json.loads((tmp_path / "route.json").read_text())
    assert written["verdict"] == "PASS"
    assert "k" * 35 not in (tmp_path / "route.json").read_text(), "the key must never be written"


def test_the_route_guard_catches_the_azure_misdispatch_of_job_66271190(monkeypatch):
    """The recorded failure: a perfect openai-compatible config and a router that dispatches to Azure."""
    from skillopt.model import route_guard as RG
    from skillopt.model.backend_config import set_optimizer_backend
    _pin(monkeypatch)
    set_optimizer_backend("openai_chat")           # the default this project walked into twice
    monkeypatch.setenv("SKILLOPT_REQUIRE_ROUTE", DEEPSEEK)
    with pytest.raises(RG.RouteMismatch) as exc:
        RG.verify()
    assert "openai_chat" in str(exc.value)


def test_the_route_guard_rejects_a_base_url_that_is_not_the_one_required(monkeypatch):
    from skillopt.model import route_guard as RG
    from skillopt.model import configure_openai_compatible
    _pin(monkeypatch)
    configure_openai_compatible(optimizer_base_url="https://api.openai.com/v1",
                                optimizer_api_key="k" * 35, optimizer_model="deepseek-flash")
    monkeypatch.setenv("SKILLOPT_REQUIRE_ROUTE", DEEPSEEK)
    with pytest.raises(RG.RouteMismatch) as exc:
        RG.verify()
    assert "api.openai.com" in str(exc.value)


def test_the_route_guard_rejects_a_model_that_is_not_the_one_required(monkeypatch):
    from skillopt.model import route_guard as RG
    from skillopt.model import configure_openai_compatible
    _pin(monkeypatch)
    configure_openai_compatible(optimizer_base_url="https://api.deepseek.com",
                                optimizer_api_key="k" * 35, optimizer_model="gpt-4o-mini")
    monkeypatch.setenv("SKILLOPT_REQUIRE_ROUTE", DEEPSEEK)
    with pytest.raises(RG.RouteMismatch) as exc:
        RG.verify()
    assert "gpt-4o-mini" in str(exc.value)


def test_a_role_required_to_be_unusable_must_have_no_key(monkeypatch):
    """The shared rollout and the held-out evaluation make no optimizer call; the job proves they cannot."""
    from skillopt.model import route_guard as RG
    _pin(monkeypatch)
    monkeypatch.setenv("SKILLOPT_REQUIRE_ROUTE", f"{EMPIRIO};optimizer=none")
    with pytest.raises(RG.RouteMismatch) as exc:
        RG.verify()
    assert "unusable" in str(exc.value)
    monkeypatch.delenv("OPTIMIZER_OPENAI_COMPATIBLE_API_KEY", raising=False)
    record = RG.verify()
    assert record["roles"]["optimizer"]["api_key_present"] is False


def test_the_dispatch_check_sends_nothing(monkeypatch):
    """It must raise before any client is built, so no HTTP attempt and no ledger claim is consumed."""
    from skillopt.model import route_guard as RG
    import skillopt.model.openai_compatible_backend as B
    _pin(monkeypatch)
    monkeypatch.setenv("SKILLOPT_REQUIRE_ROUTE", DEEPSEEK)
    built = []
    real_get = B._get_client
    monkeypatch.setattr(B, "_get_client", lambda role: built.append(role) or real_get(role))
    RG.verify()
    assert built == [], "no client may be built by the dispatch check"


def test_the_entry_points_call_the_route_guard():
    """A guard that is not wired in guarantees nothing."""
    for path in ("skillopt/engine/trainer.py", "scripts/eval_only.py"):
        with open(os.path.join(REPO, path), encoding="utf-8") as f:
            text = f.read()
        assert "route_guard" in text and "_verify_route()" in text, f"{path} does not call the route guard"


def test_one_job_cannot_eat_an_authorization_the_other_arms_still_need(tmp_path, monkeypatch):
    """A per-process ceiling beside the shared cap: a runaway arm-epoch stops at its own share."""
    env = _env(str(tmp_path), attempts=1_000, usd=100.0, block_attempts=5)
    env["SKILLOPT_LEDGER_JOB_MAX_ATTEMPTS_TARGET"] = "12"
    _apply(env, monkeypatch)
    assert _spend_until_refused() == 12
    # The shared authorization is untouched beyond what this job claimed, so a second job can still run.
    granted = LEDGER._granted()[("target", "attempts")]
    assert granted <= 15, "claims should not run far ahead of a 12-attempt ceiling"
    LEDGER._state.clear()
    LEDGER._owner = "second-job"
    assert _spend_until_refused() == 12
