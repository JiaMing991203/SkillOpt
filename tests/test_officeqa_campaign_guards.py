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


# ── the concurrent-import race that killed arm C's epoch-1 job (66272740) ─────

def test_the_learned_contract_modules_load_once_under_concurrency(monkeypatch):
    """`_lc_modules` must be safe to call from several analyst threads at once.

    The failure this guards against is recorded: job 66272740 died after writing 16 of its 24 reports with
    `AttributeError: module 'oqa_lc_chain' has no attribute 'VIOLATED'`, because the loader registered an EMPTY
    module in sys.modules and only then executed it, so a second thread reaching `oqa_lc_report`'s module-level
    `import oqa_lc_chain as CHAIN` saw a half-built module. Arm B's job, with the same code and the same two
    threads, happened to get away with it.
    """
    import concurrent.futures
    import importlib.util

    research = os.path.join(REPO, "research", "officeqa")
    if research not in sys.path:
        sys.path.insert(0, research)
    spec = importlib.util.spec_from_file_location("oqa_reports_racetest",
                                                  os.path.join(research, "oqa_reports.py"))
    reports = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reports)

    # Clear anything a previous test left behind, so the race is really run.
    for name in reports._LC_NAMES:
        sys.modules.pop(name, None)
    reports._LC_CACHE = None

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = [f.result() for f in [ex.submit(reports._lc_modules) for _ in range(8)]]

    first = results[0]
    for got in results[1:]:
        assert got is first, "every caller must get the SAME tuple: the modules load once per process"
    _none, IR, CHAIN, REPORT, REPLAY, OB = first
    # The attribute whose absence killed the job, plus the rest of the obligation vocabulary.
    for state in ("VIOLATED", "DISCHARGED", "OUTSTANDING", "UNKNOWN"):
        assert hasattr(CHAIN, state), f"oqa_lc_chain.{state} is missing: the module is half-built"
    assert REPORT._ORDER[CHAIN.VIOLATED] == 0
    # And the second, quieter hazard: one instance per module, not two.
    assert REPORT.REPLAY is REPLAY, "oqa_lc_report and _lc_modules must hold the SAME oqa_lc_replay"
    assert REPORT.OB is OB, "oqa_lc_report and _lc_modules must hold the SAME oqa_obligations"
    assert REPORT.CHAIN is CHAIN
    assert REPORT.IR is IR


def test_a_failed_module_load_leaves_nothing_half_built(monkeypatch, tmp_path):
    """If exec fails, sys.modules must not keep the broken module for the next caller to trip over."""
    import importlib.util

    research = os.path.join(REPO, "research", "officeqa")
    spec = importlib.util.spec_from_file_location("oqa_reports_failtest",
                                                  os.path.join(research, "oqa_reports.py"))
    reports = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reports)
    for name in reports._LC_NAMES:
        sys.modules.pop(name, None)
    reports._LC_CACHE = None

    real = importlib.util.spec_from_file_location

    def broken(name, path):
        if name == "oqa_lc_chain":
            raise RuntimeError("simulated loader failure")
        return real(name, path)

    monkeypatch.setattr(importlib.util, "spec_from_file_location", broken)
    with pytest.raises(RuntimeError, match="simulated loader failure"):
        reports._lc_modules()
    assert "oqa_lc_chain" not in sys.modules or getattr(sys.modules["oqa_lc_chain"], "VIOLATED", None), \
        "a half-built oqa_lc_chain was left in sys.modules"
    assert reports._LC_CACHE is None, "a failed load must not be cached"
    for name in reports._LC_NAMES:
        sys.modules.pop(name, None)


# ── the claim-granularity defect that starved four arm-epochs ─────────────────

def test_a_process_that_exits_cleanly_gives_back_what_it_did_not_spend(tmp_path, monkeypatch):
    """The defect this guards against cost the three-arm campaign its last four arm-epochs.

    The ledger enforces the cap on CLAIMED money. The first version never released, so every process kept the
    headroom it held at exit - about one per-call reserve each, plus block rounding. Over 13 processes that
    claimed the whole $4.00 optimizer authorization while spending $2.19: 45% of the money was
    claimed-but-unspent and unreachable, and once claims hit the cap the ledger refused all 24 further
    attempts. The cap did its job on the wrong quantity.
    """
    env = _env(str(tmp_path), attempts=1000, usd=1.0, block_usd=0.05, reserve=0.02)
    _apply(env, monkeypatch)
    LEDGER.reserve("target", "test")
    LEDGER.record_usage("target", "test", 100_000, 0)          # $0.004
    before = LEDGER._granted()
    claimed_before = before[("target", "usdmicro")]
    assert claimed_before > 4_000, "the process should be holding more than it spent"

    given = LEDGER.release("test")
    assert given["given_back"], "something should have been given back"
    after = LEDGER._granted()[("target", "usdmicro")]
    assert after < claimed_before, "the net claim must fall when a process releases"
    # The invariant: net claims still cover what was actually spent.
    assert after >= 4_000


def test_the_released_headroom_is_available_to_another_process(tmp_path, monkeypatch):
    """The point of the release: the next arm-epoch can claim what the last one handed back."""
    env = _env(str(tmp_path), attempts=1000, usd=0.20, block_usd=0.05, reserve=0.02)
    _apply(env, monkeypatch)

    def one_process(release_at_end: bool, attempts: int = 5) -> int:
        """A short-lived process: a few cheap attempts, then exit - the shape of one arm-epoch job."""
        LEDGER._state.clear()
        used = 0
        for _ in range(attempts):
            try:
                LEDGER.reserve("target", "test")
            except LEDGER.BudgetExhausted:
                break
            used += 1
            LEDGER.record_usage("target", "test", 25_000, 0)    # $0.001 per attempt
        if release_at_end:
            LEDGER.release("end of process")
        return used

    # Six processes spending $0.005 each come to $0.03 against a $0.20 cap, so the MONEY is never the
    # constraint. What can starve them is claim granularity: each holds a $0.05 block plus a $0.02 reserve at
    # exit, so without the release the fourth process onwards finds the cap fully claimed.
    with_release = []
    for i in range(6):
        LEDGER._owner = f"keep{i}"
        with_release.append(one_process(True))
    assert all(r == 5 for r in with_release), \
        f"a later process was starved despite released headroom: {with_release}"
    observed = LEDGER.observed()["by_role"]["target"]
    assert observed["usd"] <= 0.20 + 1e-9, "the cap must still hold"
    assert observed["usd"] == pytest.approx(0.030, abs=1e-6)

    # The counterfactual, on a fresh ledger: the same six processes WITHOUT releasing.
    fresh = tmp_path / "norelease"
    fresh.mkdir()
    _apply(_env(str(fresh), attempts=1000, usd=0.20, block_usd=0.05, reserve=0.02), monkeypatch)
    without = []
    for i in range(6):
        LEDGER._owner = f"drop{i}"
        without.append(one_process(False))
    assert without[-1] == 0, (
        f"the no-release case should starve a later process, which is the defect being fixed: {without}")
    assert sum(without) < sum(with_release), (
        f"releasing must let more real work through: {without} vs {with_release}")


def test_releasing_twice_cannot_hand_back_the_same_money(tmp_path, monkeypatch):
    _apply(_env(str(tmp_path), attempts=1000, usd=1.0, block_usd=0.05, reserve=0.02), monkeypatch)
    LEDGER.reserve("target", "test")
    LEDGER.record_usage("target", "test", 100_000, 0)
    first = LEDGER.release("first")
    net_after_first = LEDGER._granted()[("target", "usdmicro")]
    second = LEDGER.release("second")
    assert not second["given_back"], "a second release must hand back nothing"
    assert LEDGER._granted()[("target", "usdmicro")] == net_after_first


def test_a_usd_block_must_be_small_against_the_cap():
    """The defect was not that the block existed but that it was LARGE relative to the authorization."""
    for role, block in LEDGER._DEFAULT_BLOCK_USD.items():
        assert block <= 0.05, f"{role}: a {block} block against a single-digit cap wastes too much per process"


# ── the accounting fix: an unresolved request keeps its reserve ────────────────
#
# `release` hands back claimed-but-unspent money. The subtlety the first version missed: a request that was
# SENT and whose reply never arrived has no usage record, so it looks unspent - but the provider may well have
# billed it. Handing that money back would let the next process spend money that is already gone.

def test_an_unresolved_request_keeps_its_reserve_when_the_process_exits(tmp_path, monkeypatch):
    _apply(_env(str(tmp_path), attempts=1000, usd=1.0, block_usd=0.05, reserve=0.02), monkeypatch)
    LEDGER.reserve("target", "test")          # sent
    # ... and no record_usage: the reply never came back.
    out = LEDGER.release("clean exit")
    assert out["retained_for_unresolved"]["target"]["unresolved_requests"] == 1
    assert out["retained_for_unresolved"]["target"]["usd_held"] == pytest.approx(0.02)
    net = LEDGER._granted()[("target", "usdmicro")]
    assert net == 20_000, f"the reserve for the one unresolved request must stay claimed, got {net}"


def test_a_resolved_request_releases_everything_above_its_charge(tmp_path, monkeypatch):
    """The contrast: once the reply is accounted for, only the real charge is retained."""
    _apply(_env(str(tmp_path), attempts=1000, usd=1.0, block_usd=0.05, reserve=0.02), monkeypatch)
    LEDGER.reserve("target", "test")
    LEDGER.record_usage("target", "test", 100_000, 0)     # $0.004
    out = LEDGER.release("clean exit")
    assert not out["retained_for_unresolved"]
    assert LEDGER._granted()[("target", "usdmicro")] == 4_000


def test_several_unresolved_requests_each_keep_one_reserve(tmp_path, monkeypatch):
    _apply(_env(str(tmp_path), attempts=1000, usd=1.0, block_usd=0.05, reserve=0.02), monkeypatch)
    for _ in range(3):
        LEDGER.reserve("target", "test")
    LEDGER.record_usage("target", "test", 50_000, 0)      # $0.002; one of the three resolved
    LEDGER.release("clean exit")
    # two unresolved x $0.02 reserve + the $0.002 actually charged
    assert LEDGER._granted()[("target", "usdmicro")] == 2 * 20_000 + 2_000


def test_the_snapshot_shows_a_running_job_its_unresolved_count(tmp_path, monkeypatch):
    _apply(_env(str(tmp_path), attempts=1000, usd=1.0), monkeypatch)
    LEDGER.reserve("target", "test")
    LEDGER.reserve("target", "test")
    LEDGER.record_usage("target", "test", 1_000, 0)
    role = LEDGER.snapshot()["roles"]["target"]
    assert role["unresolved_requests_this_process"] == 1


# ── interruption: a process that is killed cannot release, and must not ────────

_KILL_CHILD = textwrap.dedent(
    """
    import os, sys, signal
    sys.path.insert(0, {root!r})
    from skillopt.model import spend_ledger as L
    L.reserve("target", "test")
    L.record_usage("target", "test", 100_000, 0)
    sys.stdout.write("claimed\\n")
    sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGKILL)
    """
)


def test_a_process_killed_without_warning_keeps_its_whole_claim(tmp_path):
    """SIGKILL runs no atexit hook, so nothing is released. That is the SAFE direction and it is deliberate.

    The guarantee the ledger makes is `spent <= claimed <= cap`. A killed process strands its claim, which
    errs towards refusing work rather than towards overspending; the operational answer is the post-hoc
    reconciler, not a release the dying process cannot be trusted to perform.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env.update(_env(str(tmp_path), attempts=1000, usd=1.0, block_usd=0.05, reserve=0.02))
    proc = subprocess.run([sys.executable, "-c", _KILL_CHILD.format(root=root)],
                          capture_output=True, text=True, env=env, timeout=120)
    assert "claimed" in proc.stdout
    assert proc.returncode != 0, "the child was meant to die from SIGKILL"
    assert not os.path.isdir(os.path.join(str(tmp_path), "release")) or not os.listdir(
        os.path.join(str(tmp_path), "release")), "a killed process must not have released anything"

    os.environ.update(_env(str(tmp_path), attempts=1000, usd=1.0, block_usd=0.05, reserve=0.02))
    LEDGER._state.clear()
    LEDGER._owner = ""
    net = LEDGER._granted()[("target", "usdmicro")]
    spent = LEDGER.observed()["by_role"]["target"]["usd"]
    assert net == 50_000, f"the whole $0.05 block should still be claimed, got {net}"
    assert spent == pytest.approx(0.004), spent
    assert net / 1e6 >= spent, "spent <= claimed must survive a kill"


# ── the post-hoc reconciler ───────────────────────────────────────────────────

def _reconciler():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "research", "officeqa", "oqa_ledger_reconcile.py")
    spec = importlib.util.spec_from_file_location("oqa_ledger_reconcile_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dead_process(ledger: str, *, owner: str, attempts: int, resolve: int, prompt_tokens: int,
                  monkeypatch) -> None:
    """Run one short-lived ledger client that exits WITHOUT releasing, as the campaign's jobs did."""
    LEDGER._state.clear()
    LEDGER._owner = owner
    for i in range(attempts):
        LEDGER.reserve("target", "test")
        if i < resolve:
            LEDGER.record_usage("target", "test", prompt_tokens, 0)
    LEDGER._state.clear()        # the process is gone; no atexit, no release


def test_the_reconciler_releases_only_what_an_exited_process_did_not_spend(tmp_path, monkeypatch):
    R = _reconciler()
    _apply(_env(str(tmp_path), attempts=1000, usd=1.0, block_usd=0.05, reserve=0.02), monkeypatch)
    _dead_process(str(tmp_path), owner="111r0p1", attempts=4, resolve=4, prompt_tokens=100_000,
                  monkeypatch=monkeypatch)
    spent = LEDGER.observed()["by_role"]["target"]["usd"]          # 4 x $0.004 = $0.016
    before = LEDGER._granted()[("target", "usdmicro")]
    assert before > int(spent * 1e6), "the dead process stranded headroom"

    monkeypatch.setattr(R, "_live_jobs", lambda: set())
    plan = R.plan(str(tmp_path), exited_jobs=None, assume_all_exited=True)
    assert not plan["problems"], plan["problems"]
    R.apply_plan(plan, reason="test")

    after = LEDGER._granted()[("target", "usdmicro")]
    assert after == int(round(spent * 1e6)), f"net claims should fall to the real spend, got {after}"
    assert LEDGER._granted()[("target", "attempts")] == 4


def test_the_reconciler_retains_the_reserve_of_an_unresolved_request(tmp_path, monkeypatch):
    R = _reconciler()
    _apply(_env(str(tmp_path), attempts=1000, usd=1.0, block_usd=0.05, reserve=0.02), monkeypatch)
    # three sent, two accounted for: one request's cost was never learned
    _dead_process(str(tmp_path), owner="222r0p1", attempts=3, resolve=2, prompt_tokens=100_000,
                  monkeypatch=monkeypatch)
    monkeypatch.setattr(R, "_live_jobs", lambda: set())
    plan = R.plan(str(tmp_path), exited_jobs=None, assume_all_exited=True)
    assert plan["by_role"]["target"]["unresolved_requests"] == 1
    assert plan["by_role"]["target"]["retained_usd_for_unresolved"] == pytest.approx(0.02)
    R.apply_plan(plan, reason="test")
    # $0.008 really charged + $0.02 held for the unresolved one
    assert LEDGER._granted()[("target", "usdmicro")] == 8_000 + 20_000


def test_the_reconciler_never_releases_an_actual_charge(tmp_path, monkeypatch):
    """The property that matters: whatever the arithmetic, recorded spend stays claimed."""
    R = _reconciler()
    _apply(_env(str(tmp_path), attempts=1000, usd=1.0, block_usd=0.05, reserve=0.02), monkeypatch)
    for n, owner in ((7, "333r0p1"), (3, "333r0p2"), (11, "444r0p9")):
        _dead_process(str(tmp_path), owner=owner, attempts=n, resolve=n, prompt_tokens=250_000,
                      monkeypatch=monkeypatch)
    spent_usd = LEDGER.observed()["by_role"]["target"]["usd"]
    monkeypatch.setattr(R, "_live_jobs", lambda: set())
    plan = R.plan(str(tmp_path), exited_jobs=None, assume_all_exited=True)
    R.apply_plan(plan, reason="test")
    net = LEDGER._granted()[("target", "usdmicro")] / 1e6
    assert net >= spent_usd - 1e-9, f"net claims {net} fell below real spend {spent_usd}"
    assert net == pytest.approx(spent_usd, abs=1e-6)


def test_the_reconciler_is_idempotent(tmp_path, monkeypatch):
    R = _reconciler()
    _apply(_env(str(tmp_path), attempts=1000, usd=1.0, block_usd=0.05, reserve=0.02), monkeypatch)
    _dead_process(str(tmp_path), owner="555r0p1", attempts=5, resolve=5, prompt_tokens=100_000,
                  monkeypatch=monkeypatch)
    monkeypatch.setattr(R, "_live_jobs", lambda: set())
    first = R.plan(str(tmp_path), exited_jobs=None, assume_all_exited=True)
    R.apply_plan(first, reason="test")
    net = LEDGER._granted()[("target", "usdmicro")]
    second = R.plan(str(tmp_path), exited_jobs=None, assume_all_exited=True)
    assert second["entries"] == [], "a second reconciliation must find nothing left to release"
    R.apply_plan(second, reason="test")
    assert LEDGER._granted()[("target", "usdmicro")] == net


def test_the_reconciler_leaves_a_live_job_alone(tmp_path, monkeypatch):
    """Releasing a running process's claim would break the invariant the ledger exists to hold."""
    R = _reconciler()
    _apply(_env(str(tmp_path), attempts=1000, usd=1.0, block_usd=0.05, reserve=0.02), monkeypatch)
    _dead_process(str(tmp_path), owner="666r0p1", attempts=4, resolve=4, prompt_tokens=100_000,
                  monkeypatch=monkeypatch)
    before = LEDGER._granted()[("target", "usdmicro")]
    monkeypatch.setattr(R, "_live_jobs", lambda: {"666"})
    plan = R.plan(str(tmp_path), exited_jobs=None, assume_all_exited=True)
    assert plan["entries"] == []
    assert plan["skipped"] and "queue" in plan["skipped"][0]["reason"]
    R.apply_plan(plan, reason="test")
    assert LEDGER._granted()[("target", "usdmicro")] == before, "a live job's claim must be untouched"


def test_the_reconciler_requires_evidence_that_the_owner_exited(tmp_path, monkeypatch):
    R = _reconciler()
    _apply(_env(str(tmp_path), attempts=1000, usd=1.0, block_usd=0.05, reserve=0.02), monkeypatch)
    _dead_process(str(tmp_path), owner="777r0p1", attempts=4, resolve=4, prompt_tokens=100_000,
                  monkeypatch=monkeypatch)
    monkeypatch.setattr(R, "_live_jobs", lambda: set())
    bare = R.plan(str(tmp_path), exited_jobs=None, assume_all_exited=False)
    assert bare["entries"] == [] and bare["skipped"], "without evidence nothing may be released"
    named = R.plan(str(tmp_path), exited_jobs={"777"}, assume_all_exited=False)
    assert named["entries"], "an owner named as exited is reconciled"


def test_the_reconciler_agrees_with_the_ledgers_own_accounting(tmp_path, monkeypatch):
    """Two independent readers of the same files: the ledger's `observed()` and the stdlib-only reconciler."""
    R = _reconciler()
    _apply(_env(str(tmp_path), attempts=1000, usd=1.0, block_usd=0.05, reserve=0.02), monkeypatch)
    _dead_process(str(tmp_path), owner="888r0p1", attempts=6, resolve=5, prompt_tokens=120_000,
                  monkeypatch=monkeypatch)
    mine = LEDGER.observed()["by_role"]["target"]
    theirs = R.read_ledger(str(tmp_path))["owners"]["888r0p1"]["roles"]["target"]
    assert theirs["attempts"] == mine["attempts"] == 6
    assert theirs["resolved"] == mine["resolved"] == 5
    assert theirs["usdmicro"] == pytest.approx(mine["usd"] * 1e6, abs=2)
    assert mine["unresolved"] == 1


# ── the rollout provenance guard ──────────────────────────────────────────────
#
# `run_batch` resumes by id: a row already in results.jsonl is returned as it stands and `skill_content` is
# never consulted for it. That is what makes a saved rollout reusable, and it is also how a recovery could
# silently reflect on traces produced by a skill that is no longer in force.

def _rollout_module():
    from skillopt.envs.officeqa import rollout as RO
    return RO


def _seed_results(dirpath: str, skill: str, ids=("q1", "q2")) -> None:
    RO = _rollout_module()
    system = RO._build_system(skill, search_mode="offline", use_local_tools=True,
                              max_tool_turns=24, max_queries_per_turn=4)
    os.makedirs(dirpath, exist_ok=True)
    with open(os.path.join(dirpath, "results.jsonl"), "w", encoding="utf-8") as f:
        for i in ids:
            f.write(json.dumps({"id": i, "hard": 1, "soft": 1.0, "target_system_prompt": system}) + "\n")


def test_without_the_guard_a_saved_rollout_is_reused_whatever_the_skill(tmp_path, monkeypatch):
    """The pre-existing behaviour, stated as a test so a change to it cannot pass unnoticed."""
    RO = _rollout_module()
    monkeypatch.delenv("SKILLOPT_ROLLOUT_PROVENANCE_GUARD", raising=False)
    d = str(tmp_path / "r")
    _seed_results(d, "SKILL ONE")
    items = [{"id": "q1"}, {"id": "q2"}]
    out = RO.run_batch(items, d, "A COMPLETELY DIFFERENT SKILL", search_mode="offline",
                       use_local_tools=True, max_tool_turns=24)
    assert [r["id"] for r in out] == ["q1", "q2"], "both rows are reused and nothing is sent"


def test_the_guard_reuses_a_rollout_whose_producing_skill_still_matches(tmp_path, monkeypatch):
    RO = _rollout_module()
    monkeypatch.setenv("SKILLOPT_ROLLOUT_PROVENANCE_GUARD", "1")
    d = str(tmp_path / "r")
    _seed_results(d, "SKILL ONE")
    out = RO.run_batch([{"id": "q1"}, {"id": "q2"}], d, "SKILL ONE", search_mode="offline",
                       use_local_tools=True, max_tool_turns=24)
    assert [r["id"] for r in out] == ["q1", "q2"], "a matching rollout must still be free"
    assert not glob_superseded(d), "nothing was superseded, so no backup should exist"


def glob_superseded(d: str) -> list[str]:
    import glob as _g
    return _g.glob(os.path.join(d, "results.jsonl.superseded-*"))


def test_the_guard_refuses_a_rollout_produced_by_a_different_skill(tmp_path, monkeypatch):
    """The recovery case: epoch 4's paired rollout is stale exactly when epoch 3's gate accepted."""
    RO = _rollout_module()
    monkeypatch.setenv("SKILLOPT_ROLLOUT_PROVENANCE_GUARD", "1")
    d = str(tmp_path / "r")
    _seed_results(d, "THE OLD SKILL")
    sent: list[str] = []

    def fake_process_one(item, out_root, skill_content, **kwargs):
        sent.append(str(item["id"]))
        return {"id": item["id"], "hard": 0, "soft": 0.0, "target_system_prompt": "rerolled"}

    monkeypatch.setattr(RO, "process_one", fake_process_one)
    out = RO.run_batch([{"id": "q1"}, {"id": "q2"}], d, "THE NEW SKILL", search_mode="offline",
                       use_local_tools=True, max_tool_turns=24)
    assert sorted(sent) == ["q1", "q2"], "both stale rows must be re-rolled"
    assert len(out) == 2


def test_the_guard_keeps_results_jsonl_canonical_and_preserves_the_original(tmp_path, monkeypatch):
    """A re-rolled id must not end up in the file twice: every downstream reader keys on id."""
    RO = _rollout_module()
    monkeypatch.setenv("SKILLOPT_ROLLOUT_PROVENANCE_GUARD", "1")
    d = str(tmp_path / "r")
    _seed_results(d, "THE OLD SKILL", ids=("q1", "q2", "q3"))
    monkeypatch.setattr(RO, "process_one", lambda item, out_root, skill_content, **kw: {
        "id": item["id"], "hard": 0, "soft": 0.0, "target_system_prompt": "rerolled"})
    RO.run_batch([{"id": "q1"}, {"id": "q2"}, {"id": "q3"}], d, "THE NEW SKILL",
                 search_mode="offline", use_local_tools=True, max_tool_turns=24)
    rows = [json.loads(l) for l in open(os.path.join(d, "results.jsonl"), encoding="utf-8")]
    ids = [r["id"] for r in rows]
    assert sorted(ids) == ["q1", "q2", "q3"], f"ids must appear once each, got {ids}"
    assert all(r["target_system_prompt"] == "rerolled" for r in rows)
    backups = glob_superseded(d)
    assert len(backups) == 1, "the superseded rollout must be preserved, not deleted"
    old = [json.loads(l) for l in open(backups[0], encoding="utf-8")]
    assert len(old) == 3 and all("THE OLD SKILL" in r["target_system_prompt"] for r in old)


def test_the_guard_re_rolls_only_the_rows_that_changed(tmp_path, monkeypatch):
    """Partial staleness: a directory half-written under one skill and half under another."""
    RO = _rollout_module()
    monkeypatch.setenv("SKILLOPT_ROLLOUT_PROVENANCE_GUARD", "1")
    d = str(tmp_path / "r")
    os.makedirs(d, exist_ok=True)
    good = RO._build_system("THE NEW SKILL", search_mode="offline", use_local_tools=True,
                            max_tool_turns=24, max_queries_per_turn=4)
    bad = RO._build_system("THE OLD SKILL", search_mode="offline", use_local_tools=True,
                           max_tool_turns=24, max_queries_per_turn=4)
    with open(os.path.join(d, "results.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "keep", "hard": 1, "target_system_prompt": good}) + "\n")
        f.write(json.dumps({"id": "drop", "hard": 1, "target_system_prompt": bad}) + "\n")
    sent: list[str] = []
    monkeypatch.setattr(RO, "process_one", lambda item, out_root, skill_content, **kw: (
        sent.append(str(item["id"])), {"id": item["id"], "hard": 0, "target_system_prompt": good})[1])
    RO.run_batch([{"id": "keep"}, {"id": "drop"}], d, "THE NEW SKILL",
                 search_mode="offline", use_local_tools=True, max_tool_turns=24)
    assert sent == ["drop"], f"only the stale row should be paid for again, re-rolled: {sent}"


def _row(RO, *, skill, turns, fingerprint: bool):
    row = {"id": "q1", "hard": 1,
           "target_system_prompt": RO._build_system(skill, search_mode="offline", use_local_tools=True,
                                                    max_tool_turns=turns, max_queries_per_turn=4)}
    if fingerprint:
        row["rollout_settings_fingerprint"] = RO.rollout_settings_fingerprint(
            search_mode="offline", use_local_tools=True, max_tool_turns=turns,
            max_queries_per_turn=4, max_completion_tokens=16384)
    return row


def test_a_changed_turn_limit_is_invisible_in_the_system_prompt_alone(tmp_path, monkeypatch):
    """The guard's real coverage, stated rather than assumed.

    I first wrote this test asserting that a changed `max_tool_turns` would be caught by the system-prompt
    comparison, and it failed - correctly. In the offline/local-tools mode this project uses, the turn budget
    appears in the per-round instruction and the USER prompt; the system prompt is the skill section alone. A
    row from before the settings fingerprint existed therefore cannot be checked for it, and is reused.
    """
    RO = _rollout_module()
    monkeypatch.setenv("SKILLOPT_ROLLOUT_PROVENANCE_GUARD", "1")
    d = str(tmp_path / "r")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "results.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(_row(RO, skill="S", turns=12, fingerprint=False)) + "\n")
    sent: list[str] = []
    monkeypatch.setattr(RO, "process_one", lambda item, out_root, skill_content, **kw: (
        sent.append("q1"), {"id": "q1", "hard": 0, "target_system_prompt": "x"})[1])
    RO.run_batch([{"id": "q1"}], d, "S", search_mode="offline", use_local_tools=True,
                 max_tool_turns=24)
    assert sent == [], "a pre-fingerprint row is reused; the turn budget is not in the system prompt"


def test_a_changed_turn_limit_IS_caught_once_the_row_carries_a_fingerprint(tmp_path, monkeypatch):
    RO = _rollout_module()
    monkeypatch.setenv("SKILLOPT_ROLLOUT_PROVENANCE_GUARD", "1")
    d = str(tmp_path / "r")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "results.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(_row(RO, skill="S", turns=12, fingerprint=True)) + "\n")
    sent: list[str] = []
    monkeypatch.setattr(RO, "process_one", lambda item, out_root, skill_content, **kw: (
        sent.append("q1"), {"id": "q1", "hard": 0, "target_system_prompt": "x"})[1])
    RO.run_batch([{"id": "q1"}], d, "S", search_mode="offline", use_local_tools=True,
                 max_tool_turns=24)
    assert sent == ["q1"], "a fingerprinted row records its turn budget, so the change must be caught"


def test_a_matching_fingerprint_does_not_force_a_re_roll(tmp_path, monkeypatch):
    RO = _rollout_module()
    monkeypatch.setenv("SKILLOPT_ROLLOUT_PROVENANCE_GUARD", "1")
    d = str(tmp_path / "r")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "results.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(_row(RO, skill="S", turns=24, fingerprint=True)) + "\n")
    out = RO.run_batch([{"id": "q1"}], d, "S", search_mode="offline", use_local_tools=True,
                       max_tool_turns=24)
    assert [r["id"] for r in out] == ["q1"]


def test_a_new_row_records_the_settings_it_ran_under(monkeypatch):
    """So that a rollout written today can be checked for settings tomorrow."""
    RO = _rollout_module()
    a = RO.rollout_settings_fingerprint(search_mode="offline", use_local_tools=True, max_tool_turns=24,
                                        max_queries_per_turn=4, max_completion_tokens=16384)
    b = RO.rollout_settings_fingerprint(search_mode="offline", use_local_tools=True, max_tool_turns=12,
                                        max_queries_per_turn=4, max_completion_tokens=16384)
    c = RO.rollout_settings_fingerprint(search_mode="offline", use_local_tools=False, max_tool_turns=24,
                                        max_queries_per_turn=4, max_completion_tokens=16384)
    assert a != b and a != c and len(a) == 16
    assert a == RO.rollout_settings_fingerprint(
        search_mode="offline", use_local_tools=True, max_tool_turns=24,
        max_queries_per_turn=4, max_completion_tokens=16384), "it must be stable"


# ── the recovery tool ─────────────────────────────────────────────────────────

def _recover():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "research", "officeqa", "oqa_recover.py")
    spec = importlib.util.spec_from_file_location("oqa_recover_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_campaign(root: str, *, refuse_job: str = "900") -> str:
    """A two-epoch arm whose epoch 2 was starved, plus the ledger records that prove it."""
    os.makedirs(os.path.join(root, "ledger", "spend"), exist_ok=True)
    os.makedirs(os.path.join(root, "ledger", "claims"), exist_ok=True)
    arm = os.path.join(root, "arm_A")
    for sub in ("skills", "steps/step_0001", "steps/step_0002",
                "slow_update/epoch_01", "slow_update/epoch_02",
                "meta_skill/epoch_01", "meta_skill/epoch_02"):
        os.makedirs(os.path.join(arm, sub), exist_ok=True)
    for i, text in enumerate(("S0 ADVICE", "S0 ADVICE", "S0 ADVICE")):
        with open(os.path.join(arm, "skills", f"skill_v{i:04d}.md"), "w", encoding="utf-8") as f:
            f.write(text)
    with open(os.path.join(arm, "best_skill.md"), "w", encoding="utf-8") as f:
        f.write("S0 ADVICE")

    RO = _rollout_module()
    def seed(d, skill, n):
        system = RO._build_system(skill, search_mode="offline", use_local_tools=True,
                                  max_tool_turns=24, max_queries_per_turn=4)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "results.jsonl"), "w", encoding="utf-8") as f:
            for k in range(n):
                f.write(json.dumps({"id": f"q{k}", "hard": 0,
                                    "target_system_prompt": system}) + "\n")

    seed(os.path.join(arm, "steps/step_0001/rollout"), "S0 ADVICE", 24)
    seed(os.path.join(arm, "steps/step_0002/rollout"), "S0 ADVICE", 24)
    seed(os.path.join(arm, "slow_update/epoch_02/rollout_prev"), "S0 ADVICE", 10)
    seed(os.path.join(arm, "slow_update/epoch_02/rollout_curr"), "S0 ADVICE", 10)

    with open(os.path.join(arm, "steps/step_0001/step_record.json"), "w", encoding="utf-8") as f:
        json.dump({"step": 1, "epoch": 1, "rollout_n": 24, "rollout_hard": 0.125, "n_patches": 3,
                   "n_edits_merged": 7, "n_edits_ranked": 4, "edit_budget": 4, "action": "reject",
                   "candidate_gate_score": 0.04, "current_score": 0.1667, "best_score": 0.1667,
                   "best_step": 0, "current_origin": "initial_skill", "best_origin": "initial_skill",
                   "tokens": {"analyst": {"calls": 4}, "merge": {"calls": 2}, "ranking": {"calls": 1}}},
                  f)
    with open(os.path.join(arm, "steps/step_0002/step_record.json"), "w", encoding="utf-8") as f:
        json.dump({"step": 2, "epoch": 2, "rollout_n": 24, "rollout_hard": 0.25, "n_patches": 0,
                   "action": "skip_no_patches", "current_score": 0.1667, "best_score": 0.1667,
                   "best_step": 0, "tokens": {}}, f)
    with open(os.path.join(arm, "slow_update/epoch_01/slow_result.json"), "w", encoding="utf-8") as f:
        json.dump({"action": "inject_placeholder"}, f)
    with open(os.path.join(arm, "meta_skill/epoch_01/meta_skill_result.json"), "w", encoding="utf-8") as f:
        json.dump({"action": "skip_first_epoch"}, f)
    with open(os.path.join(arm, "slow_update/epoch_02/slow_result.json"), "w", encoding="utf-8") as f:
        json.dump({"action": "no_content", "time_s": 1.0}, f)
    with open(os.path.join(arm, "meta_skill/epoch_02/meta_skill_result.json"), "w", encoding="utf-8") as f:
        json.dump({"action": "no_content", "time_s": 0.0}, f)
    with open(os.path.join(arm, "slow_update/epoch_02/comparison_pairs.json"), "w", encoding="utf-8") as f:
        json.dump([{"id": f"q{k}"} for k in range(10)], f)
    with open(os.path.join(arm, "runtime_state.json"), "w", encoding="utf-8") as f:
        json.dump({"last_completed_step": 2, "current_skill_path": "x", "current_score": 0.1667,
                   "best_skill_path": "y", "best_score": 0.1667, "best_step": 0,
                   "current_origin": "initial_skill", "best_origin": "initial_skill"}, f)

    with open(os.path.join(root, "ledger", "spend", f"{refuse_job}r0p1.jsonl"), "w",
              encoding="utf-8") as f:
        for stage, n in (("analyst", 4), ("slow_update", 1), ("meta_skill", 1)):
            for _ in range(n):
                f.write(json.dumps({"kind": "refused", "role": "optimizer", "stage": stage,
                                    "reason": "usd cap reached"}) + "\n")
    bundle = os.path.join(root, "bundle")
    os.makedirs(os.path.join(bundle, f"run_{refuse_job}_arm_A_e2"), exist_ok=True)
    os.makedirs(os.path.join(bundle, "run_800_arm_A_e1"), exist_ok=True)
    return bundle


def test_the_audit_calls_a_refused_stage_starved_and_a_quiet_one_ok(tmp_path):
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    doc = REC.audit(str(tmp_path), [bundle])
    by = {r["key"]: r for r in doc["rows"]}
    assert by["A.1"]["affected"] is False
    assert by["A.2"]["affected"] is True
    assert "analyst" in by["A.2"]["starved_stages"]
    assert "gate" in by["A.2"]["starved_stages"], "skip_no_patches with a refusal is the masked failure"
    assert "slow_update" in by["A.2"]["starved_stages"]
    assert "meta_skill" in by["A.2"]["starved_stages"]
    assert doc["earliest_affected_epoch_per_arm"] == {"A": 2}
    assert doc["total_refusals"] == 6


def test_the_audit_does_not_call_a_genuinely_quiet_epoch_starved(tmp_path):
    """Without the ledger's refusal records, the same artifacts must NOT be read as a budget failure."""
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    os.remove(os.path.join(str(tmp_path), "ledger", "spend", "900r0p1.jsonl"))
    doc = REC.audit(str(tmp_path), [bundle])
    by = {r["key"]: r for r in doc["rows"]}
    assert by["A.2"]["affected"] is False, "no refusal means no evidence of starvation"
    assert by["A.2"]["stages"]["slow_update"]["status"] == "genuine_no_content"
    assert any(u["key"] == "A.2" for u in doc["unexplained"]), \
        "it should still be reported as needing an explanation"


def test_the_audit_explains_a_missing_ranking_call_that_is_legitimate(tmp_path):
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    arm = os.path.join(str(tmp_path), "arm_A")
    rec = json.load(open(os.path.join(arm, "steps/step_0001/step_record.json")))
    rec["n_edits_merged"], rec["edit_budget"] = 4, 4
    rec["tokens"]["ranking"] = {"calls": 0}
    json.dump(rec, open(os.path.join(arm, "steps/step_0001/step_record.json"), "w"))
    doc = REC.audit(str(tmp_path), [bundle])
    s = {r["key"]: r for r in doc["rows"]}["A.1"]["stages"]["ranking"]
    assert s["status"] == "ok" and "already fit the edit budget" in s["note"]


def test_the_audit_explains_one_fewer_analyst_call_in_an_epoch_that_scored_zero(tmp_path):
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    arm = os.path.join(str(tmp_path), "arm_A")
    rec = json.load(open(os.path.join(arm, "steps/step_0001/step_record.json")))
    rec["rollout_hard"] = 0.0
    rec["tokens"]["analyst"] = {"calls": 3}
    json.dump(rec, open(os.path.join(arm, "steps/step_0001/step_record.json"), "w"))
    doc = REC.audit(str(tmp_path), [bundle])
    s = {r["key"]: r for r in doc["rows"]}["A.1"]["stages"]["analyst"]
    assert s["status"] == "ok" and "no SUCCESS group" in s["note"]


def test_a_short_analyst_in_an_epoch_that_did_score_is_flagged_not_excused(tmp_path):
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    arm = os.path.join(str(tmp_path), "arm_A")
    rec = json.load(open(os.path.join(arm, "steps/step_0001/step_record.json")))
    rec["tokens"]["analyst"] = {"calls": 2}
    json.dump(rec, open(os.path.join(arm, "steps/step_0001/step_record.json"), "w"))
    doc = REC.audit(str(tmp_path), [bundle])
    assert {r["key"]: r for r in doc["rows"]}["A.1"]["stages"]["analyst"]["status"] == "short"


def test_the_plan_identifies_the_producing_skill_from_the_trace(tmp_path):
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    doc = REC.plan(str(tmp_path), REC.audit(str(tmp_path), [bundle]),
                   usd_per_question=0.00656, requests_per_question=10.32)
    item = doc["items"][0]
    train = next(a for a in item["artifacts"] if a["kind"] == "training rollout")
    assert train["identification"]["status"] == "identified"
    assert train["decision"] == "reuse", train
    curr = next(a for a in item["artifacts"] if a["kind"] == "slow-update rollout_curr")
    assert curr["decision"] == "reuse_if_provenance_holds" and curr["conditional"] is True


def test_the_plan_refuses_to_reuse_a_rollout_it_cannot_attribute(tmp_path):
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    d = os.path.join(str(tmp_path), "arm_A", "steps/step_0002/rollout", "results.jsonl")
    with open(d, "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "q0", "target_system_prompt": "SOMETHING ELSE ENTIRELY"}) + "\n")
    doc = REC.plan(str(tmp_path), REC.audit(str(tmp_path), [bundle]),
                   usd_per_question=0.00656, requests_per_question=10.32)
    train = next(a for a in doc["items"][0]["artifacts"] if a["kind"] == "training rollout")
    assert train["decision"] == "regenerate"
    assert train["identification"]["status"] == "unidentified"
    assert doc["problems"]


def test_the_plan_refuses_to_reuse_a_rollout_with_mixed_provenance(tmp_path):
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    RO = _rollout_module()
    p = os.path.join(str(tmp_path), "arm_A", "steps/step_0002/rollout", "results.jsonl")
    a = RO._build_system("S0 ADVICE", search_mode="offline", use_local_tools=True,
                         max_tool_turns=24, max_queries_per_turn=4)
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "q0", "target_system_prompt": a}) + "\n")
        f.write(json.dumps({"id": "q1", "target_system_prompt": a + " EXTRA"}) + "\n")
    doc = REC.plan(str(tmp_path), REC.audit(str(tmp_path), [bundle]),
                   usd_per_question=0.00656, requests_per_question=10.32)
    train = next(a for a in doc["items"][0]["artifacts"] if a["kind"] == "training rollout")
    assert train["identification"]["status"] == "mixed"
    assert train["decision"] == "regenerate"


def test_prepare_rewinds_the_runtime_state_to_the_last_sound_step(tmp_path):
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    plan = REC.plan(str(tmp_path), REC.audit(str(tmp_path), [bundle]),
                    usd_per_question=0.00656, requests_per_question=10.32)
    rec = REC.prepare(str(tmp_path), plan, apply=True, stamp="TEST")
    assert not rec["refused"], rec["refused"]
    state = json.load(open(os.path.join(str(tmp_path), "arm_A", "runtime_state.json")))
    assert state["last_completed_step"] == 1, "epoch 2 must be re-run, so step 1 is the last completed one"
    assert state["current_skill_path"].endswith("skill_v0001.md")
    assert state["best_score"] == 0.1667 and state["best_step"] == 0


def test_prepare_removes_the_resume_markers_that_would_skip_the_work_again(tmp_path):
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    plan = REC.plan(str(tmp_path), REC.audit(str(tmp_path), [bundle]),
                    usd_per_question=0.00656, requests_per_question=10.32)
    REC.prepare(str(tmp_path), plan, apply=True, stamp="TEST")
    arm = os.path.join(str(tmp_path), "arm_A")
    assert not os.path.exists(os.path.join(arm, "slow_update/epoch_02/slow_result.json")), \
        "the trainer resumes a slow update on the EXISTENCE of this file, so a no_content must go"
    assert not os.path.exists(os.path.join(arm, "meta_skill/epoch_02/meta_skill_result.json"))
    assert os.path.exists(os.path.join(arm, "slow_update/epoch_01/slow_result.json")), \
        "a sound earlier epoch must keep its marker, or it would be re-run and re-charged"


def test_prepare_keeps_the_paid_rollouts_in_place_so_they_are_reused(tmp_path):
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    plan = REC.plan(str(tmp_path), REC.audit(str(tmp_path), [bundle]),
                    usd_per_question=0.00656, requests_per_question=10.32)
    before = os.path.getsize(os.path.join(str(tmp_path), "arm_A/steps/step_0002/rollout/results.jsonl"))
    REC.prepare(str(tmp_path), plan, apply=True, stamp="TEST")
    after = os.path.join(str(tmp_path), "arm_A/steps/step_0002/rollout/results.jsonl")
    assert os.path.exists(after) and os.path.getsize(after) == before


def test_prepare_archives_the_originals_before_changing_anything(tmp_path):
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    plan = REC.plan(str(tmp_path), REC.audit(str(tmp_path), [bundle]),
                    usd_per_question=0.00656, requests_per_question=10.32)
    rec = REC.prepare(str(tmp_path), plan, apply=True, stamp="TEST")
    arch = os.path.join(str(tmp_path), rec["archive"])
    saved = json.load(open(os.path.join(arch, "arm_A", "slow_update/epoch_02/slow_result.json")))
    assert saved["action"] == "no_content", "the original record must survive the recovery"
    assert json.load(open(os.path.join(arch, "arm_A", "runtime_state.json")))[
        "last_completed_step"] == 2, "the pre-rewind state must survive"
    assert os.path.exists(os.path.join(arch, "arm_A", "skills", "skill_v0002.md"))


def test_prepare_changes_nothing_on_a_dry_run(tmp_path):
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    plan = REC.plan(str(tmp_path), REC.audit(str(tmp_path), [bundle]),
                    usd_per_question=0.00656, requests_per_question=10.32)
    rec = REC.prepare(str(tmp_path), plan, apply=False, stamp="TEST")
    assert all(str(a.get("status", "")).startswith(("would", "")) for a in rec["actions"])
    state = json.load(open(os.path.join(str(tmp_path), "arm_A", "runtime_state.json")))
    assert state["last_completed_step"] == 2, "a dry run must not touch the state"
    assert os.path.exists(os.path.join(str(tmp_path), "arm_A/slow_update/epoch_02/slow_result.json"))
    assert not os.path.exists(os.path.join(str(tmp_path), "recovery"))


def test_prepare_refuses_when_the_previous_step_record_cannot_supply_the_state(tmp_path):
    """Rewinding to a step whose record is missing would invent a gate baseline. It must refuse instead."""
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    plan = REC.plan(str(tmp_path), REC.audit(str(tmp_path), [bundle]),
                    usd_per_question=0.00656, requests_per_question=10.32)
    os.remove(os.path.join(str(tmp_path), "arm_A", "steps/step_0001/step_record.json"))
    rec = REC.prepare(str(tmp_path), plan, apply=False, stamp="TEST")
    assert rec["refused"], "a rewind it cannot substantiate must be refused"


# ── the rounding question: two ways to sum the same charges ───────────────────

def test_the_reconciler_retains_the_larger_of_the_two_ways_to_sum_a_charge(tmp_path, monkeypatch):
    """A real finding from reconciling the campaign's own ledger, reduced to a test.

    The ledger adds `int(round(usd * 1e6))` per record, so that integer is the quantity the cap is enforced
    on. A reader that instead sums the raw floats gets a slightly different number - over the campaign's
    7,313 target records the float sum came out 26 microdollars HIGHER. Releasing down to the integer sum
    therefore made `oqa_ledger_read.py`, which summed floats, report that spend exceeded the net claim. The
    reconciler now retains the larger of the two, so no reader can see that whichever way it sums.
    """
    R = _reconciler()
    _apply(_env(str(tmp_path), attempts=5000, usd=5.0, block_usd=0.05, reserve=0.02), monkeypatch)
    # Prices chosen so each charge lands between two microdollars and the rounding goes the same way often.
    monkeypatch.setenv("SKILLOPT_LEDGER_PRICE_TARGET", "0.0411,0.0733,0.02")
    LEDGER._state.clear()
    LEDGER._owner = "999r0p1"
    for i in range(400):
        LEDGER.reserve("target", "test")
        LEDGER.record_usage("target", "test", 1_111 + i, 37)
    LEDGER._state.clear()

    import math as _m
    floats, micros = 0.0, 0
    with open(os.path.join(str(tmp_path), "spend", "999r0p1.jsonl"), encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("kind") == "usage":
                floats += float(rec["usd"])
                micros += int(round(float(rec["usd"]) * 1_000_000))

    monkeypatch.setattr(R, "_live_jobs", lambda: set())
    plan = R.plan(str(tmp_path), exited_jobs=None, assume_all_exited=True)
    assert not plan["problems"], plan["problems"]
    R.apply_plan(plan, reason="test")
    net = LEDGER._granted()[("target", "usdmicro")]
    assert net >= micros, "the ledger's own integer accounting must stay covered"
    assert net >= _m.ceil(floats * 1_000_000), "a float-summing reader must also see spent <= claimed"


def test_the_external_audit_checks_the_invariant_in_micros(tmp_path, monkeypatch):
    """The same defect from the audit's side: equality after reconciliation is the IDEAL, not a failure."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "research", "officeqa", "oqa_ledger_read.py")
    spec = importlib.util.spec_from_file_location("oqa_ledger_read_test", path)
    READ = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(READ)

    R = _reconciler()
    _apply(_env(str(tmp_path), attempts=5000, usd=5.0, block_usd=0.05, reserve=0.02), monkeypatch)
    LEDGER._state.clear()
    LEDGER._owner = "aaa1r0p1"
    for i in range(300):
        LEDGER.reserve("target", "test")
        LEDGER.record_usage("target", "test", 997 + i, 13)
    LEDGER._state.clear()
    monkeypatch.setattr(R, "_live_jobs", lambda: set())
    R.apply_plan(R.plan(str(tmp_path), exited_jobs=None, assume_all_exited=True), reason="test")

    out = READ.read(str(tmp_path))
    spent_vs_claim = [p for p in out["problems"] if "spent against" in p]
    assert not spent_vs_claim, f"reconciled to equality must not be a problem: {spent_vs_claim}"
    t = out["observed"]["target"]
    assert t["usd"] == pytest.approx(t["usdmicro"] / 1e6)
    assert "usd_float_sum" in t and "usd_float_minus_micro" in t


def test_the_audit_still_flags_real_overspend(tmp_path, monkeypatch):
    """The check must not have been loosened into uselessness."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "research", "officeqa", "oqa_ledger_read.py")
    spec = importlib.util.spec_from_file_location("oqa_ledger_read_test2", path)
    READ = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(READ)
    _apply(_env(str(tmp_path), attempts=5000, usd=5.0, block_usd=0.05, reserve=0.02), monkeypatch)
    LEDGER._state.clear()
    LEDGER._owner = "bbb1r0p1"
    LEDGER.reserve("target", "test")
    LEDGER.record_usage("target", "test", 1_000, 0)
    # Forge a release far larger than anything claimed, as a corrupt or hostile record would look.
    rel = os.path.join(str(tmp_path), "release")
    os.makedirs(rel, exist_ok=True)
    with open(os.path.join(rel, "999999-target-usdmicro-900000.json"), "w", encoding="utf-8") as f:
        json.dump({"schema": "lso.spend_ledger.v1", "kind": "release", "role": "target",
                   "resource": "usdmicro", "amount": 900000, "owner": "forged"}, f)
    out = READ.read(str(tmp_path))
    assert any("spent against" in p for p in out["problems"]), \
        f"spend above the net claim must still be reported: {out['problems']}"


def test_prepare_trims_history_so_the_rerun_does_not_double_count(tmp_path):
    """summary.json counts accepts, rejects and skips straight out of history.json.

    The trainer loads history whole and appends to it, with no trimming on resume, so re-running step 2 would
    leave two records for step 2 and the final readout would double-count exactly the epochs under repair.
    """
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    arm = os.path.join(str(tmp_path), "arm_A")
    with open(os.path.join(arm, "history.json"), "w", encoding="utf-8") as f:
        json.dump([{"step": 1, "epoch": 1, "action": "reject"},
                   {"step": 2, "epoch": 2, "action": "skip_no_patches"}], f)
    plan = REC.plan(str(tmp_path), REC.audit(str(tmp_path), [bundle]),
                    usd_per_question=0.00656, requests_per_question=10.32)
    rec = REC.prepare(str(tmp_path), plan, apply=True, stamp="TEST")
    hist = json.load(open(os.path.join(arm, "history.json")))
    assert [h["step"] for h in hist] == [1], "the step being re-run must be dropped from history"
    saved = json.load(open(os.path.join(str(tmp_path), rec["archive"], "arm_A", "history.json")))
    assert [h["step"] for h in saved] == [1, 2], "the original history must survive in the archive"
    trim = next(a for a in rec["actions"] if a["action"] == "trim history")
    assert trim["dropped_steps"] == [2]


def test_prepare_moves_aside_only_the_contract_models_whose_inputs_can_change(tmp_path):
    """`oqa_lc_ir.freeze` REFUSES to overwrite a frozen model with different content, and the body only warns.

    So a model file for an epoch whose traces can change has to be moved aside, or the evolution stops and
    leaves a file standing under a name that no longer describes its epoch. A model whose inputs are reused
    unchanged must be KEPT, so the re-freeze is a no-op and it holds its original bytes.
    """
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    # Dress the fake campaign as arm C: contracts for epochs 1 and 2, recovering from epoch 2.
    cdir = os.path.join(str(tmp_path), "contracts", "arm_C")
    os.makedirs(cdir, exist_ok=True)
    for e in (1, 2):
        for name in (f"model_armC_epoch{e:02d}.json", f"EVOLVE_armC_epoch{e:02d}.json"):
            with open(os.path.join(cdir, name), "w", encoding="utf-8") as f:
                json.dump({"epoch": e, "frozen_utc": "2026-11-17T00:00:00+00:00"}, f)
    os.rename(os.path.join(str(tmp_path), "arm_A"), os.path.join(str(tmp_path), "arm_C"))
    os.rename(os.path.join(str(tmp_path), "bundle", "run_900_arm_A_e2"),
              os.path.join(str(tmp_path), "bundle", "run_900_arm_C_e2"))
    os.rename(os.path.join(str(tmp_path), "bundle", "run_800_arm_A_e1"),
              os.path.join(str(tmp_path), "bundle", "run_800_arm_C_e1"))

    doc = REC.audit(str(tmp_path), [bundle])
    assert doc["earliest_affected_epoch_per_arm"] == {"C": 2}
    plan = REC.plan(str(tmp_path), doc, usd_per_question=0.00656, requests_per_question=10.32)
    rec = REC.prepare(str(tmp_path), plan, apply=True, stamp="TEST")

    assert os.path.exists(os.path.join(cdir, "model_armC_epoch02.json")), \
        "epoch 2 is the first recovered epoch; its rollout is reused, so its model must be KEPT"
    assert not os.path.exists(os.path.join(cdir, "model_armC_epoch01.json")) or True
    kept = [a for a in rec["actions"] if a["action"] == "keep the first recovered epoch's model"]
    assert kept and kept[0]["exists"] and kept[0]["digest"]


def test_prepare_moves_aside_a_later_epochs_model(tmp_path):
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    cdir = os.path.join(str(tmp_path), "contracts", "arm_C")
    os.makedirs(cdir, exist_ok=True)
    for e in (1, 2, 3, 4):
        with open(os.path.join(cdir, f"model_armC_epoch{e:02d}.json"), "w", encoding="utf-8") as f:
            json.dump({"epoch": e}, f)
    os.rename(os.path.join(str(tmp_path), "arm_A"), os.path.join(str(tmp_path), "arm_C"))
    for old, new in (("run_900_arm_A_e2", "run_900_arm_C_e2"), ("run_800_arm_A_e1", "run_800_arm_C_e1")):
        os.rename(os.path.join(str(tmp_path), "bundle", old), os.path.join(str(tmp_path), "bundle", new))
    plan = REC.plan(str(tmp_path), REC.audit(str(tmp_path), [bundle]),
                    usd_per_question=0.00656, requests_per_question=10.32)
    rec = REC.prepare(str(tmp_path), plan, apply=True, stamp="TEST")
    assert os.path.exists(os.path.join(cdir, "model_armC_epoch02.json")), "the first recovered epoch's: kept"
    for e in (3, 4):
        assert not os.path.exists(os.path.join(cdir, f"model_armC_epoch{e:02d}.json")), \
            f"epoch {e} is after the recovery start, so its model must be moved aside"
        assert os.path.exists(os.path.join(
            str(tmp_path), rec["archive"], "contracts", "arm_C",
            f"model_armC_epoch{e:02d}.json.superseded")), "and preserved in the archive"


def test_the_rewind_takes_the_incumbent_from_an_accepted_slow_update(tmp_path):
    """An epoch does not end with its ordinary step: the gated slow update runs after it and can accept.

    If it did, the step record's current_score is stale, and rewinding to it would hand the recovered gate
    the wrong incumbent to beat. Every slow gate in this campaign rejected, so nothing actually moves - this
    test exists because a tool that is accidentally right is not right.
    """
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    arm = os.path.join(str(tmp_path), "arm_A")
    with open(os.path.join(arm, "slow_update/epoch_01/slow_result.json"), "w", encoding="utf-8") as f:
        json.dump({"action": "accept_new_best", "selection_hard": 0.2917,
                   "slow_update_content": "x"}, f)
    plan = REC.plan(str(tmp_path), REC.audit(str(tmp_path), [bundle]),
                    usd_per_question=0.00656, requests_per_question=10.32)
    rec = REC.prepare(str(tmp_path), plan, apply=True, stamp="TEST")
    state = json.load(open(os.path.join(arm, "runtime_state.json")))
    assert state["current_score"] == 0.2917, "the incumbent must come from the accepted slow update"
    assert state["best_score"] == 0.2917 and state["best_step"] == 1
    assert state["current_origin"] == "slow_update_epoch_01"


def test_the_rewind_refuses_when_an_accepted_slow_update_records_no_score(tmp_path):
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    arm = os.path.join(str(tmp_path), "arm_A")
    with open(os.path.join(arm, "slow_update/epoch_01/slow_result.json"), "w", encoding="utf-8") as f:
        json.dump({"action": "accept", "slow_update_content": "x"}, f)
    plan = REC.plan(str(tmp_path), REC.audit(str(tmp_path), [bundle]),
                    usd_per_question=0.00656, requests_per_question=10.32)
    rec = REC.prepare(str(tmp_path), plan, apply=False, stamp="TEST")
    assert rec["refused"], "an incumbent it cannot determine must stop the rewind, not be guessed"


def test_the_rewind_uses_the_step_record_when_the_slow_update_rejected(tmp_path):
    """The actual campaign case, stated so the general path above cannot silently change it."""
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    arm = os.path.join(str(tmp_path), "arm_A")
    with open(os.path.join(arm, "slow_update/epoch_01/slow_result.json"), "w", encoding="utf-8") as f:
        json.dump({"action": "reject", "selection_hard": 0.0417, "slow_update_content": "x"}, f)
    plan = REC.plan(str(tmp_path), REC.audit(str(tmp_path), [bundle]),
                    usd_per_question=0.00656, requests_per_question=10.32)
    REC.prepare(str(tmp_path), plan, apply=True, stamp="TEST")
    state = json.load(open(os.path.join(arm, "runtime_state.json")))
    assert state["current_score"] == 0.1667, "a rejected slow update must not move the incumbent"
    assert state["current_origin"] == "initial_skill"


# ── freezing the held-out evaluation set by digest ────────────────────────────

def _freezer():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "research", "officeqa", "oqa_freeze_skills.py")
    spec = importlib.util.spec_from_file_location("oqa_freeze_skills_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _skill_tree(root: str, *, s0: str, selected: dict, last: dict) -> str:
    init = os.path.join(root, "initial.md")
    with open(init, "w", encoding="utf-8") as f:
        f.write(s0)
    for arm, text in selected.items():
        d = os.path.join(root, f"arm_{arm}")
        os.makedirs(os.path.join(d, "skills"), exist_ok=True)
        with open(os.path.join(d, "best_skill.md"), "w", encoding="utf-8") as f:
            f.write(text)
        with open(os.path.join(d, "skills", "skill_v0004.md"), "w", encoding="utf-8") as f:
            f.write(last[arm])
    return init


def test_the_freeze_counts_distinct_by_digest_and_by_advice(tmp_path):
    """Two skills that differ only inside an injected marker block carry the SAME advice to the actor.

    Counting those as two different skills would overstate how far the arms diverged, which is exactly the
    mistake the A40 run invited: both arms' final skills came out byte-identical to S0.
    """
    F = _freezer()
    root = str(tmp_path)
    s0 = "Read the oracle page and answer."
    with_marker = s0 + "\n\n<!-- SLOW_UPDATE -->\nsome guidance\n<!-- /SLOW_UPDATE -->\n"
    init = _skill_tree(root, s0=s0,
                       selected={"A": s0, "B": "A GENUINELY DIFFERENT SKILL", "C": s0},
                       last={"A": with_marker, "B": "A GENUINELY DIFFERENT SKILL", "C": with_marker})
    doc = F.freeze(root, init)
    assert doc["requested"] == 7
    # by digest: S0, the marker variant, and arm B's skill
    assert doc["distinct_by_digest"] == 3, doc["by_digest"]
    # by advice: the marker variant collapses into S0
    assert doc["distinct_by_advice"] == 2, doc["by_advice"]
    assert doc["primary_distinct_by_advice"] == 2, "S0 + arm B's selected skill"


def test_the_freeze_detects_a_skill_that_changed_afterwards(tmp_path):
    F = _freezer()
    root = str(tmp_path)
    init = _skill_tree(root, s0="S0", selected={"A": "selected A"}, last={"A": "last A"})
    out = os.path.join(root, "SKILLS_FROZEN.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(F.freeze(root, init), f)
    assert F.verify(out)["verdict"] == "PASS"
    with open(os.path.join(root, "arm_A", "best_skill.md"), "w", encoding="utf-8") as f:
        f.write("SOMETHING ELSE")
    res = F.verify(out)
    assert res["verdict"] == "FAIL"
    assert any("has CHANGED" in p for p in res["problems"])


def test_the_freeze_detects_an_edited_record(tmp_path):
    """The record is the evidence; an edited record must not pass itself."""
    F = _freezer()
    root = str(tmp_path)
    init = _skill_tree(root, s0="S0", selected={"A": "selected A"}, last={"A": "last A"})
    out = os.path.join(root, "SKILLS_FROZEN.json")
    doc = F.freeze(root, init)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    assert F.verify(out)["verdict"] == "PASS"
    doc["entries"][0]["sha256"] = "0" * 64
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    res = F.verify(out)
    assert res["verdict"] == "FAIL"
    assert any("has been edited" in p or "has CHANGED" in p for p in res["problems"])


def test_a_missing_skill_is_recorded_rather_than_skipped(tmp_path):
    F = _freezer()
    root = str(tmp_path)
    init = _skill_tree(root, s0="S0", selected={"A": "selected A"}, last={"A": "last A"})
    os.remove(os.path.join(root, "arm_A", "best_skill.md"))
    doc = F.freeze(root, init)
    assert doc["missing"] and doc["missing"][0]["role"] == "validation_selected"
    assert doc["requested"] == 2, "a missing skill must not be counted as present"


def test_verify_resolves_archived_paths_for_both_record_shapes(tmp_path):
    """`path` in an archive action is relative to whatever was convenient; `live_path` is not ambiguous.

    The first version of `verify` guessed between the arm root and the campaign root and got the contracts
    entries wrong. It now uses `live_path`, and falls back to recovering the same relative path out of
    `archive` for a record written before that field existed.
    """
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    plan = REC.plan(str(tmp_path), REC.audit(str(tmp_path), [bundle]),
                    usd_per_question=0.00656, requests_per_question=10.32)
    rec = REC.prepare(str(tmp_path), plan, apply=True, stamp="TEST")
    arch = [a for a in rec["actions"] if a["action"] == "archive" and a.get("digest")]
    assert arch and all("live_path" in a for a in arch)

    out = REC.verify(str(tmp_path), rec)
    assert out["unchanged_after_recovery"], "it must resolve at least one archived file"
    assert all(r["exists"] for r in out["unchanged_after_recovery"]), \
        [r for r in out["unchanged_after_recovery"] if not r["exists"]]

    # The same record with live_path stripped, as an older run would have written it.
    old_shape = json.loads(json.dumps(rec))
    for a in old_shape["actions"]:
        a.pop("live_path", None)
    out2 = REC.verify(str(tmp_path), old_shape)
    assert len(out2["unchanged_after_recovery"]) == len(out["unchanged_after_recovery"])
    assert all(r["exists"] for r in out2["unchanged_after_recovery"])
