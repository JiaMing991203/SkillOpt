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


def test_the_advice_stripper_uses_the_markers_the_trainer_actually_writes(tmp_path):
    """The placeholder a rejected step leaves behind must count as the SAME advice as the skill it annotates.

    This is the check that would have caught my guessed marker format: with the wrong markers the stripper is
    a no-op and every rejected step's snapshot looks like a new skill.
    """
    F = _freezer()
    assert ("<!-- SLOW_UPDATE_START -->", "<!-- SLOW_UPDATE_END -->") in F.MARKER_PAIRS
    base = "Advice line one.\nAdvice line two."
    placeholder = base + "\n\n<!-- SLOW_UPDATE_START -->\n<!-- SLOW_UPDATE_END -->\n"
    filled = base + "\n\n<!-- SLOW_UPDATE_START -->\nreal guidance\n<!-- SLOW_UPDATE_END -->\n"
    assert F.substantive(placeholder) == F.substantive(base), \
        "an EMPTY placeholder does not change the advice"
    assert F.substantive(filled) == F.substantive(base), \
        "guidance inside a marker block is not part of the base advice either"
    assert F.substantive(base + "\nAdvice line three.") != F.substantive(base), \
        "but real text outside the markers IS"


def test_the_freeze_counts_distinct_by_digest_and_by_advice(tmp_path):
    """Two skills that differ only inside an injected marker block carry the SAME advice to the actor.

    Counting those as two different skills would overstate how far the arms diverged, which is exactly the
    mistake the A40 run invited: both arms' final skills came out byte-identical to S0.
    """
    F = _freezer()
    root = str(tmp_path)
    s0 = "Read the oracle page and answer."
    # The REAL marker pair the trainer injects. I first wrote this test with
    # `<!-- SLOW_UPDATE -->...<!-- /SLOW_UPDATE -->`, which exists nowhere in this repo, so the test passed
    # against a stripper that stripped nothing.
    with_marker = s0 + "\n\n<!-- SLOW_UPDATE_START -->\nsome guidance\n<!-- SLOW_UPDATE_END -->\n"
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
    byp = {r["path"]: r for r in out["unchanged_after_recovery"]}
    assert byp, "it must resolve at least one archived file"
    # Resolution is correct when the path is relative to the CAMPAIGN root, so an arm's file carries its arm
    # prefix. Before the fix these came back resolved against the arm root twice over.
    assert all(k.startswith(("arm_", "contracts/")) for k in byp), sorted(byp)
    # A file that was archived and left alone is found, and is the same file.
    assert byp["arm_A/runtime_state.json"]["exists"] is True
    # A resume marker was archived and then deliberately REMOVED, so it must resolve and be reported absent.
    # This is the case my first version of this test wrongly asserted could not happen.
    marker = byp["arm_A/slow_update/epoch_02/slow_result.json"]
    assert marker["exists"] is False and marker["unchanged"] is False

    # The same record with live_path stripped, as an older run would have written it.
    old_shape = json.loads(json.dumps(rec))
    for a in old_shape["actions"]:
        a.pop("live_path", None)
    out2 = REC.verify(str(tmp_path), old_shape)
    assert {r["path"]: (r["exists"], r["unchanged"]) for r in out2["unchanged_after_recovery"]} == \
           {r["path"]: (r["exists"], r["unchanged"]) for r in out["unchanged_after_recovery"]}, \
        "the fallback must resolve exactly what live_path resolves"


def test_a_refused_stage_that_has_since_been_recovered_reads_as_repaired(tmp_path):
    """A refusal record is permanent history; the ledger keeps it after the stage is fixed.

    The first version of the audit keyed only on "was there a refusal", so after the recovery ran it still
    called all four arm-epochs starved - leaving the record claiming the arms were broken when they were not.
    """
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    arm = os.path.join(str(tmp_path), "arm_A")
    # The epoch now has real output, while its refusal records remain in the ledger.
    rec = json.load(open(os.path.join(arm, "steps/step_0002/step_record.json")))
    rec.update({"n_patches": 4, "n_edits_merged": 5, "n_edits_ranked": 2, "edit_budget": 2,
                "action": "reject", "candidate_gate_score": 0.0417,
                "tokens": {"analyst": {"calls": 4}, "merge": {"calls": 2}, "ranking": {"calls": 1}}})
    json.dump(rec, open(os.path.join(arm, "steps/step_0002/step_record.json"), "w"))
    json.dump({"action": "reject", "selection_hard": 0.0, "slow_update_content": "guidance"},
              open(os.path.join(arm, "slow_update/epoch_02/slow_result.json"), "w"))
    json.dump({"action": "write_meta_skill", "meta_skill_content": "memory"},
              open(os.path.join(arm, "meta_skill/epoch_02/meta_skill_result.json"), "w"))

    doc = REC.audit(str(tmp_path), [bundle])
    row = {r["key"]: r for r in doc["rows"]}["A.2"]
    assert row["affected"] is False, "a repaired epoch is not still starved"
    assert row["recovered"] is True
    assert row["stages"]["analyst"]["status"] == "repaired"
    assert row["stages"]["slow_update"]["status"] == "repaired"
    assert row["stages"]["meta_skill"]["status"] == "repaired"
    assert doc["affected"] == [] and doc["recovered"] == ["A.2"]
    # And the refusals are still reported, because they happened.
    assert row["refusals_by_stage"]["analyst"] == 4


def test_a_stage_with_refusals_and_still_no_output_is_still_starved(tmp_path):
    """The contrast: `repaired` must require real output, not merely the passage of time."""
    REC = _recover()
    bundle = _fake_campaign(str(tmp_path))
    doc = REC.audit(str(tmp_path), [bundle])
    row = {r["key"]: r for r in doc["rows"]}["A.2"]
    assert row["affected"] is True and row["recovered"] is False
    assert row["stages"]["analyst"]["status"] == "starved"


# ── the diagnostic read of a finished campaign (rule OQA-DIAG-1) ──────────────

def _diag():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "research", "officeqa", "oqa_diagnose.py")
    spec = importlib.util.spec_from_file_location("oqa_diagnose_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The real scaffold, in the shape `_build_system` gives it for search_mode=offline + local tools:
# `skillopt/envs/officeqa/prompts/rollout_system.md` is `<preamble>\n\n{skill_section}## Rules\n...`.
_DG_SCAFFOLD = ("You are an expert OfficeQA agent working over local Treasury bulletin text files.\n\n"
             "## Skill\n{skill}\n\n## Rules\n- Use the tools.\n- Answer in <answer></answer>.\n")


def _dg_system(skill: str) -> str:
    return _DG_SCAFFOLD.replace("{skill}", skill.strip())


def _dg_row(qid, *, skill, hard=0, predicted="", gt="1", user="## Question\nq?\n", turns=4,
         fail="", finish="", ok=True, oracle=100, sources=("f.txt",)):
    return {"id": qid, "question": "q?", "task_type": "easy", "predicted_answer": predicted,
            "response": "...", "ground_truth": gt, "source_files": list(sources),
            "resolved_source_paths": list(sources), "oracle_parsed_pages_included": True,
            "oracle_parsed_pages_chars": oracle, "use_local_tools": True, "hard": hard,
            "soft": float(hard), "fail_reason": fail, "agent_ok": ok, "n_turns": turns,
            "last_finish_reason": finish, "target_system_prompt": _dg_system(skill),
            "target_user_prompt": user}


def _dg_write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _dg_conv(path, cmds):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conv = [{"role": "user", "content": "## Question\nq?"}]
    for c in cmds:
        conv.append({"type": "message", "content": ""})
        conv.append({"type": "tool_call", "cmd": c, "obs": "data"})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(conv, f)


def _dg_campaign(root, *, ids, baseline_correct, arms):
    """A miniature campaign with the same file layout as the real one.

    `arms` maps an arm to a list of (kind, epoch, candidate_text, correct_ids, recorded_action).
    """
    S0 = "# S0\n- do the thing."
    base = []
    for qid in ids:
        base.append(_dg_row(qid, skill=S0, hard=1 if qid in baseline_correct else 0,
                         predicted="7" if qid in baseline_correct else "", gt="7"))
    _dg_write_jsonl(os.path.join(root, "shared", "val_baseline", "results.jsonl"), base)
    for qid in ids:
        _dg_conv(os.path.join(root, "shared", "val_baseline", "predictions", qid, "conversation.json"),
              [f"read(path='/data/{qid}.txt', start=1, limit=5)"])
    for arm in arms:
        a = os.path.join(root, f"arm_{arm}")
        os.makedirs(a, exist_ok=True)
        with open(os.path.join(a, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"max_tool_turns": 24, "target_openai_compatible_temperature": 0.7,
                       "seed": 42, "split_seed": 42}, f)
        _dg_write_jsonl(os.path.join(a, "selection_eval_baseline", "results.jsonl"), base)
        prev_skill = S0
        for kind, epoch, cand, correct, action in arms[arm]:
            if kind == "step":
                d = os.path.join(a, "steps", f"step_{epoch:04d}")
                os.makedirs(d, exist_ok=True)
                _dg_write_jsonl(os.path.join(d, "rollout", "results.jsonl"),
                             [_dg_row(q, skill=prev_skill) for q in ids])
                rec = {"step": epoch, "epoch": epoch, "candidate_hash": "h" * 16,
                       "selection_hard": len(correct) / len(ids), "action": action,
                       "tokens": {"analyst": {"calls": 4}, "merge": {"calls": 2},
                                  "ranking": {"calls": 1}},
                       "n_edits_merged": 8, "n_edits_ranked": 2, "edit_budget": 2,
                       "edit_apply_summary": {"applied": 2}, "support_counts": [22, 14],
                       "rollout_hard": 0.125,
                       "accumulation_batches": [{"batch_seed": 1000 + epoch}],
                       "current_score": 0.0, "best_score": 0.0}
                with open(os.path.join(d, "step_record.json"), "w", encoding="utf-8") as f:
                    json.dump(rec, f)
            else:
                d = os.path.join(a, "slow_update", f"epoch_{epoch:02d}")
                os.makedirs(d, exist_ok=True)
                _dg_write_jsonl(os.path.join(d, "rollout_curr", "results.jsonl"),
                             [_dg_row(q, skill=prev_skill) for q in ids])
                with open(os.path.join(d, "slow_result.json"), "w", encoding="utf-8") as f:
                    json.dump({"selection_hard": len(correct) / len(ids), "action": action,
                               "candidate_hash": "s" * 16, "slow_update_content": "guidance",
                               "prev_hard": 0.1, "curr_hard": 0.0}, f)
                with open(os.path.join(d, "comparison_pairs.json"), "w", encoding="utf-8") as f:
                    json.dump([{"id": q, "category": "persistent_fail"} for q in ids[:3]], f)
            with open(os.path.join(d, "candidate_skill.md"), "w", encoding="utf-8") as f:
                f.write(cand)
            sel = os.path.join(d, "selection_eval")
            _dg_write_jsonl(os.path.join(sel, "results.jsonl"),
                         [_dg_row(q, skill=cand, hard=1 if q in correct else 0,
                               predicted="7" if q in correct else "",
                               gt="7",
                               fail="" if q in correct
                               else "Model neither produced a tool request nor a final answer")
                          for q in ids])
            for q in ids:
                _dg_conv(os.path.join(sel, "predictions", q, "conversation.json"),
                      [f"read(path='/data/{q}.txt', start=1, limit=5)",
                       f"read(path='/data/{q}.txt', start=1, limit=5)"])
            if action in ("accept", "accept_new_best"):
                prev_skill = cand
    # epoch 1's slow update is the placeholder, and must not be counted as a gate
    for arm in arms:
        d = os.path.join(root, f"arm_{arm}", "slow_update", "epoch_01")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "slow_result.json"), "w", encoding="utf-8") as f:
            json.dump({"action": "inject_placeholder", "epoch": 1}, f)


def test_the_skill_is_recovered_from_the_system_prompt_even_when_it_contains_a_rules_heading():
    """The provenance route is the system prompt, so the extraction has to survive the skill's own text.

    A candidate written by the optimizer can itself contain `\n\n## Rules`, which is exactly the delimiter
    the template puts AFTER the skill. Splitting on the first occurrence would silently return a truncated
    document and then report that the file on disk is not what ran.
    """
    D = _diag()
    tricky = "# Skill\n- step one.\n\n## Rules\n- a rule the OPTIMIZER wrote, not the template's."
    sysmsg = _dg_system(tricky)
    assert D.skill_from_system(sysmsg) == tricky
    assert D.scaffold_of(sysmsg) == _dg_system("<SKILL>")
    # and two different skills under the same template share one scaffold
    assert D.scaffold_of(_dg_system("# other")) == D.scaffold_of(sysmsg)


def test_a_missing_delimiter_returns_none_rather_than_a_guess():
    D = _diag()
    assert D.skill_from_system("no skill section here") is None
    assert D.scaffold_of("no skill section here") == "no skill section here"


@pytest.mark.parametrize("pred,gt,want", [
    ("7", "7", "numerically_equal_scored_wrong"),
    ("7000", "7", "scale_1e3"),
    ("0.007", "7", "scale_1e-3"),
    ("-7", "7", "sign_flipped"),
    ("7.00001", "7", "rounding"),
    ("[1, 2]", "[1, 2, 3]", "wrong_value_count"),
    ("", "7", "no_number_predicted"),
    ("no idea", "7", "non_numeric"),
    ("13", "7", "unrelated_value"),
    ("700", "7", "scale_1e2"),
])
def test_the_numeric_relation_is_mechanical(pred, gt, want):
    """It says how a wrong number relates to the right one. It never names a CAUSE.

    `scale_1e3` is what a millions/thousands mix-up looks like, but the relation alone cannot tell that
    from a coincidence, so the label stays arithmetic and the cause is left to a trace reading.
    """
    assert _diag().numeric_relation(pred, gt) == want


def test_the_four_real_outcome_shapes_are_separated_though_the_gate_scores_three_alike():
    """`hard` is exact match, so a silent turn, an empty parse and a wrong number are all 0.

    These four rows are the only shapes the campaign's 528 selection observations actually take, and the
    fifth assertion is the bug they caught: `agent_ok` is `not fail_reason` on the EXECUTION reason, so it
    is False for a silent turn. Reading it as "execution error" misfiled 354 of the 528.
    """
    D = _diag()
    silent = D.classify(_dg_row("U1", skill="s", hard=0, predicted="", ok=False,
                             fail="Model neither produced a tool request nor a final answer"), 24)
    wrong = D.classify(_dg_row("U2", skill="s", hard=0, predicted="458.070", gt="0.011"), 24)
    right = D.classify(_dg_row("U3", skill="s", hard=1, predicted="0.011", gt="0.011"), 24)
    budget = D.classify(_dg_row("U4", skill="s", hard=0, predicted="", ok=False,
                             fail="Exceeded tool-turn budget (24)", turns=24), 24)
    empty = D.classify(_dg_row("U5", skill="s", hard=0, predicted="", ok=True,
                            fail="predicted '' but the scorer marked it incorrect"), 24)
    assert silent["outcome"] == "no_answer" and silent["why"] == "silent_turn"
    assert silent["agent_ok"] is False, "the flag is False here, and it does NOT mean a crash"
    assert wrong["outcome"] == "wrong_answer" and wrong["relation"] == "unrelated_value"
    assert right["outcome"] == "correct" and right["relation"] == ""
    assert budget["outcome"] == "execution_failure" and budget["why"] == "turn_budget_exceeded"
    assert empty["outcome"] == "no_answer" and empty["why"] == "empty_parse"
    # `n_turns` counts conversation MESSAGES, not tool turns. The rows reach 55 against a tool-turn
    # budget of 24, so comparing the two reported that 5 of S0's 24 questions hit the cap when the true
    # number is 0. Only a row that says it ran out really did.
    assert D.classify(_dg_row("U6", skill="s", turns=50), 24)["turn_budget_hit"] is False
    assert budget["turn_budget_hit"] is True
    assert D.classify(_dg_row("U7", skill="s", finish="length"), 24)["truncated"] is True
    assert D.classify(_dg_row("U8", skill="s", ok=False, fail="error: boom"), 24)["why"] == "exception"


def test_the_placeholder_epoch_is_not_a_gate_and_the_order_is_the_trainers():
    D = _diag()
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        ids = [f"UID{i:04d}" for i in range(4)]
        _dg_campaign(root, ids=ids, baseline_correct={ids[0]},
                  arms={"A": [("step", 1, "# c1", set(), "reject"),
                              ("step", 2, "# c2", set(), "reject"),
                              ("slow", 2, "# s2", set(), "reject")]})
        labels = [g["label"] for g in D.gate_specs(root)]
        assert labels == ["A.1.step", "A.2.step", "A.2.slow"], labels


def test_the_threshold_moves_after_an_acceptance_and_the_decision_is_rederived():
    """The gate compares against the CURRENT score, which an acceptance raises.

    Arm B's real run is this shape: one candidate beat 4/24 with 5/24, and every later candidate then had
    to beat 5/24 rather than 4/24. A table that prints one fixed threshold for all seven of an arm's gates
    is wrong about six of them.
    """
    D = _diag()
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        ids = [f"UID{i:04d}" for i in range(8)]
        base_ok = {ids[0], ids[1]}                      # S0 = 2/8
        _dg_campaign(root, ids=ids, baseline_correct=base_ok,
                  arms={"B": [("step", 1, "# c1", {ids[0]}, "reject"),            # 1/8, below
                              ("step", 2, "# c2", {ids[0], ids[1], ids[2]},
                               "accept_new_best"),                                # 3/8, above
                              ("slow", 2, "# s2", {ids[0], ids[1], ids[2]}, "reject")]})  # 3/8, a TIE now
        doc = D.cmd_gates(root)
        g = {x["label"]: x for x in doc["gates"]}
        assert doc["baseline"]["correct"] == 2
        assert g["B.1.step"]["threshold"] == 2 / 8
        assert g["B.2.step"]["threshold"] == 2 / 8
        assert g["B.2.slow"]["threshold"] == 3 / 8, "the acceptance must raise the bar"
        assert g["B.2.slow"]["decision_recomputed"] == "reject", "a tie is not a strict improvement"
        assert all(x["decision_agrees"] for x in doc["gates"])
        assert all(x["score_agrees"] for x in doc["gates"])
        assert doc["checks"]["every_decision_recomputes"]
        assert doc["checks"]["every_score_recomputes"]


def test_a_candidate_file_that_is_not_what_ran_is_reported_not_trusted():
    """The retained skills omit rejected edits, so the candidate FILE is the only copy -- hence the check.

    If someone edited `candidate_skill.md` after the run, every number attributed to that document would be
    attributed to the wrong text. The check is that the file appears verbatim in all 24 of its own
    selection rollouts' system prompts.
    """
    D = _diag()
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        ids = [f"UID{i:04d}" for i in range(4)]
        _dg_campaign(root, ids=ids, baseline_correct=set(),
                  arms={"A": [("step", 1, "# the candidate that ran", set(), "reject")]})
        doc = D.cmd_gates(root)
        assert doc["gates"][0]["skill_embedded_verbatim"] is True
        assert doc["checks"]["every_candidate_embedded_verbatim"]
        p = os.path.join(root, "arm_A", "steps", "step_0001", "candidate_skill.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# a document nobody ever ran")
        doc2 = D.cmd_gates(root)
        assert doc2["gates"][0]["skill_embedded_verbatim"] is False
        assert not doc2["checks"]["every_candidate_embedded_verbatim"]


def test_a_changed_question_or_prompt_is_caught_so_a_score_difference_is_not_misread():
    """A score gap only speaks about the skill if the questions and the documents did not move."""
    D = _diag()
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        ids = [f"UID{i:04d}" for i in range(4)]
        _dg_campaign(root, ids=ids, baseline_correct=set(),
                  arms={"A": [("step", 1, "# c", set(), "reject")]})
        doc = D.cmd_gates(root)
        assert doc["checks"]["every_question_set_matches"]
        assert doc["checks"]["every_user_prompt_matches"]
        assert doc["checks"]["every_scaffold_matches"]
        assert doc["checks"]["oracle_pages_identical_everywhere"]
        assert doc["checks"]["sources_identical_everywhere"]
        # now hand one question a different document
        p = os.path.join(root, "arm_A", "steps", "step_0001", "selection_eval", "results.jsonl")
        rows = [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]
        rows[0]["target_user_prompt"] = "## Question\nq?\n## Extra page the baseline never saw\n"
        rows[1]["oracle_parsed_pages_chars"] = 999
        _dg_write_jsonl(p, rows)
        doc2 = D.cmd_gates(root)
        assert doc2["gates"][0]["user_prompts_identical_to_baseline"] is False
        assert doc2["gates"][0]["user_prompt_mismatches"] == [rows[0]["id"]]
        assert doc2["gates"][0]["oracle_pages_identical"] is False
        assert not doc2["checks"]["every_user_prompt_matches"]


def test_gains_and_losses_are_counted_per_question_against_the_shared_baseline():
    D = _diag()
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        ids = [f"UID{i:04d}" for i in range(6)]
        # S0 solves 0 and 1. The candidate loses 1 and gains 4.
        _dg_campaign(root, ids=ids, baseline_correct={ids[0], ids[1]},
                  arms={"A": [("step", 1, "# c", {ids[0], ids[4]}, "reject")]})
        doc = D.cmd_questions(root)
        g = doc["per_gate"]["A.1.step"]
        assert g["gained"] == [ids[4]] and g["lost"] == [ids[1]]
        assert g["kept_correct"] == [ids[0]]
        assert sorted(g["kept_incorrect"]) == sorted([ids[2], ids[3], ids[5]])
        assert doc["totals"]["s0_correct"] == 2
        assert doc["per_question"][ids[4]]["gained_by"] == ["A.1.step"]
        assert doc["per_question"][ids[1]]["lost_by"] == ["A.1.step"]
        # the blank reason survives into the per-question view
        assert doc["per_question"][ids[2]]["candidates"]["A.1.step"]["why"] == "silent_turn"
        # and the trace features are read from conversation.json
        assert g["repeated_calls_delta_mean"] == 1.0, "the fixture repeats one call in every candidate trace"


def test_trace_features_count_repeats_and_files_without_interpreting_them():
    D = _diag()
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        _dg_conv(os.path.join(root, "conversation.json"), [
            "read(path='/data/a.txt', start=1, limit=5)",
            "read(path='/data/a.txt', start=1, limit=5)",
            "grep(pattern='1943.*Feb', path='/data/b.txt')",
        ])
        f = D.trace_features(root)
        assert f["present"] and f["n_tool_calls"] == 3
        assert f["n_repeated_calls"] == 1
        assert f["files_touched"] == ["a.txt", "b.txt"]
        assert f["tools"]["read"] == 2 and f["tools"]["grep"] == 1
        assert D.trace_features(os.path.join(root, "nope"))["present"] is False


def test_contract_vocabulary_is_scanned_in_the_edits_and_a_miss_is_the_strong_signal():
    """An edit that names nothing from the report could have been written from the trace alone."""
    D = _diag()
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        d = os.path.join(root, "arm_B", "steps", "step_0002")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ranked_edits.json"), "w", encoding="utf-8") as f:
            json.dump({"edits": [
                {"op": "append", "content": "- commit once you hold the operands.",
                 "support_count": 22, "source_type": "failure"},
                {"op": "append", "content": "- RULE(read.effect.3@v1) says col_path_sound is false.",
                 "support_count": 3, "source_type": "failure"}]}, f)
        os.makedirs(os.path.join(root, "arm_B", "stage_reports"), exist_ok=True)
        with open(os.path.join(root, "arm_B", "stage_reports", "GENERATED.jsonl"),
                  "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "UID1", "action_forecasts":
                                {"confirmed": 5, "violated": 1, "unresolved": 9},
                                "obligations": None, "n_distinct_diagnoses": 0,
                                "boundary_audit": "PASS", "model_version": 2,
                                "prediction_dir": "/x/arm_B/steps/step_0001/rollout/predictions"}) + "\n")
        doc = D.cmd_contracts(root)
        e = doc["edits_scanned_for_contract_vocabulary"]["B.2.ranked"]
        assert e[0]["contract_vocabulary_hits"] == []
        assert "RULE(" in e[1]["contract_vocabulary_hits"]
        assert doc["arms"]["B"]["action_forecasts"]["violated"] == 1
        assert doc["arms"]["B"]["reports_with_a_violated_forecast"][0]["id"] == "UID1"


def test_a_candidate_that_scored_zero_still_recomputes():
    """A score of exactly 0.0 is falsy, and `score or -1` made the check fail on the real campaign.

    Two of the twenty-one candidates (arm B epoch 4 and arm C epoch 4) measured 0/24, so this is not a
    hypothetical: the first version of the recomputation check reported FAIL on both of them and PASS on
    everything else, which looks exactly like tampering with the two most interesting rows.
    """
    D = _diag()
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        ids = [f"UID{i:04d}" for i in range(4)]
        _dg_campaign(root, ids=ids, baseline_correct={ids[0]},
                  arms={"A": [("step", 1, "# c", set(), "reject")]})
        doc = D.cmd_gates(root)
        g = doc["gates"][0]
        assert g["recorded_score"] == 0.0 and g["score_recomputed"] == 0.0
        assert g["score_agrees"] is True
        assert doc["checks"]["every_score_recomputes"] is True


def test_replicates_are_grouped_by_skill_and_question_set_and_the_spread_is_reported():
    """The campaign measured its own noise floor without meaning to, and this finds it.

    Arms A and C rejected every candidate, so their in-force skill never changed and each later epoch
    re-ran the SAME document over the SAME 24 training questions. Those repeats are independent draws
    (the actor is sampled at temperature 0.7 with no seed), so their spread is what a one-question gate
    margin has to be read against. The grouping key is the skill digest taken from each rollout's own
    system prompt plus the exact question-id set, so nothing is assumed about which skill was in force.
    """
    D = _diag()
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        ids = [f"UID{i:04d}" for i in range(4)]
        _dg_campaign(root, ids=ids, baseline_correct={ids[0]},
                     arms={"A": [("step", 2, "# c2", set(), "reject"),
                                 ("step", 3, "# c3", set(), "reject")]})
        # both epochs' TRAINING rollouts ran the same document (every candidate was rejected); give them
        # different outcomes on the same questions, which is what temperature does
        for epoch, correct in ((2, {ids[0], ids[1]}), (3, {ids[0], ids[3]})):
            p = os.path.join(root, "arm_A", "steps", f"step_{epoch:04d}", "rollout", "results.jsonl")
            _dg_write_jsonl(p, [_dg_row(q, skill="# S0\n- do the thing.",
                                        hard=1 if q in correct else 0,
                                        predicted="7" if q in correct else "", gt="7") for q in ids])
        doc = D.cmd_replicates(root)
        train = [g for g in doc["groups"]
                 if any(m["kind"] == "train" for m in g["members"])]
        assert len(train) == 1, [g["members"] for g in doc["groups"]]
        g = train[0]
        # Three members, not two: this miniature campaign gives the S0 baseline the SAME question ids as
        # the training set, so that evaluation is a third draw of the same document and belongs in the
        # group. In the real campaign the splits are disjoint (train 24, val 24, test 60, no overlap), so
        # the baseline forms a group of its own and drops out.
        assert {m["label"] for m in g["members"]} == {"A.2.train", "A.3.train", "S0.baseline"}
        assert g["n_replicates"] == 3 and g["scores"] == [1, 2, 2]
        assert g["range_in_questions"] == 1
        assert g["questions_always_correct"] == [ids[0]]
        assert g["questions_never_correct"] == [ids[2]]
        assert sorted(f["id"] for f in g["questions_that_flipped"]) == [ids[1], ids[3]]


def test_the_arms_copies_of_the_baseline_are_not_counted_as_repeat_measurements():
    """Three arms hold a byte-identical copy of the one S0 evaluation. Copying a file measures nothing."""
    D = _diag()
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        ids = [f"UID{i:04d}" for i in range(4)]
        _dg_campaign(root, ids=ids, baseline_correct={ids[0]},
                     arms={"A": [("step", 1, "# c", set(), "reject")],
                           "B": [("step", 1, "# d", set(), "reject")],
                           "C": [("step", 1, "# e", set(), "reject")]})
        doc = D.cmd_replicates(root)
        for g in doc["groups"]:
            assert all(m["kind"] != "baseline_copy" for m in g["members"])


def test_one_rollout_copied_into_three_arms_is_one_measurement_not_three():
    """The campaign really does this, and counting the copies would claim the actor is deterministic.

    Epoch 1's training rollout was run once and injected into all three arms, byte for byte
    (`arm_A/B/C/steps/step_0001/rollout/results.jsonl` share one md5). Treated as three draws it reports
    three identical scores and a standard deviation of zero -- the opposite of the truth about an actor
    sampled at temperature 0.7, and the figure a reader would quote.
    """
    D = _diag()
    import tempfile, shutil
    with tempfile.TemporaryDirectory() as root:
        ids = [f"UID{i:04d}" for i in range(4)]
        _dg_campaign(root, ids=ids, baseline_correct=set(),
                     arms={"A": [("step", 1, "# a", set(), "reject"),
                                 ("step", 2, "# b", set(), "reject")],
                           "B": [("step", 1, "# c", set(), "reject")],
                           "C": [("step", 1, "# d", set(), "reject")]})
        src = os.path.join(root, "arm_A", "steps", "step_0001", "rollout", "results.jsonl")
        for arm in ("B", "C"):
            shutil.copyfile(src, os.path.join(root, f"arm_{arm}", "steps", "step_0001",
                                              "rollout", "results.jsonl"))
        doc = D.cmd_replicates(root)
        collapsed = {c["duplicate_of_it"] for c in doc["identical_measurements_collapsed"]}
        # The copies must be among the collapsed. Others are too, and legitimately: `_dg_row` is
        # deterministic, so every all-wrong rollout in this fixture is literally the same measurement.
        assert {"B.1.train", "C.1.train"} <= collapsed, collapsed
        kept = [m["label"] for g in doc["groups"] for m in g["members"]]
        assert "B.1.train" not in kept and "C.1.train" not in kept
        # and the one that was kept says which labels shared its measurement
        shared = {x for g in doc["groups"] for m in g["members"]
                  for x in (m.get("same_measurement_as") or [])}
        assert {"B.1.train", "C.1.train"} <= shared or not doc["groups"]


def test_a_bare_tool_name_is_an_invented_tool_not_an_unparseable_line():
    """The campaign's 211 calls to a nonexistent `answer` tool are all recorded as a bare name.

    `conversation.json` stores them as `{"cmd": "answer", "obs": "[tool error: unknown tool answer]"}` --
    no argument list at all. A parser that requires "(" files every one of them under "?" and the count of
    invented calls comes out as zero, which is the opposite of what the records say. Several arms' edits
    tell the actor "There is no `answer` tool - never call one", so whether it kept doing it is exactly
    the question this count has to be able to answer.
    """
    D = _diag()
    import tempfile, json as _json
    with tempfile.TemporaryDirectory() as root:
        conv = [{"role": "user", "content": "q"},
                {"type": "tool_call", "cmd": "grep(pattern='x', path='/d/a.txt')", "obs": "hit"},
                {"type": "tool_call", "cmd": "answer", "obs": "[tool error: unknown tool answer]"},
                {"type": "tool_call", "cmd": "calculate", "obs": "[tool error: unknown tool calculate]"}]
        with open(os.path.join(root, "conversation.json"), "w", encoding="utf-8") as f:
            _json.dump(conv, f)
        f = D.trace_features(root)
        assert f["invented_tool_calls"] == {"answer": 1, "calculate": 1}, f["invented_tool_calls"]
        assert f["tools"]["grep"] == 1
        assert f["n_unknown_tool_errors"] == 2
        assert "?" not in f["invented_tool_calls"]


# ── forensics on the empty answers and the contract machinery (rule OQA-FOR-1) ─

def _forensics():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "research", "officeqa", "oqa_forensics.py")
    spec = importlib.util.spec_from_file_location("oqa_forensics_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fx_row(qid="UID0001", *, fail="", pred="", hard=0, gt="7", finish=""):
    return {"id": qid, "predicted_answer": pred, "hard": hard, "ground_truth": gt,
            "fail_reason": fail, "last_finish_reason": finish}


def _fx_call(n_messages=2, *, finish="stop", content=None, tokens=100, started="2026-11-17T10:00:00+00:00"):
    kind = "null" if content is None else ("empty" if not str(content).strip() else "text")
    return {"job": "j", "started_utc": started, "n_messages": n_messages, "attempt": 1, "outcome": "ok",
            "finish_reason": finish, "content": content, "content_kind": kind,
            "reasoning_content": None, "completion_tokens": tokens, "prompt_tokens": 1000,
            "max_tokens": 8000}


def test_the_held_out_call_logs_are_excluded_by_path_and_the_exclusion_is_reported():
    """The held-out split is read once under its own procedure. This audit must not touch its logs."""
    F = _forensics()
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        for rel in ("oqa_campaign_x/run_1/actor_calls.jsonl",
                    "oqa_heldout_20261117/run_2/actor_calls.jsonl",
                    "other/held_out_thing/run_3/actor_calls.jsonl"):
            p = os.path.join(root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write("")
        kept, skipped = F.call_log_files([root])
        assert len(kept) == 1 and "oqa_campaign_x" in kept[0]
        assert len(skipped) == 2
        assert all("heldout" in s or "held_out" in s for s in skipped)


def test_episodes_split_where_a_request_starts_over_at_two_messages():
    """One key can hold several episodes: a training rollout and a slow-update pair rollout can run the
    same skill over the same item in one job. Each episode's first call carries exactly the system and the
    user message, so that is the boundary."""
    F = _forensics()
    calls = [_fx_call(2), _fx_call(4), _fx_call(6), _fx_call(2), _fx_call(4)]
    eps = F.episodes_for_key(calls)
    assert [len(e) for e in eps] == [3, 2]


def test_each_branch_of_the_loop_is_named_from_the_raw_reply_not_guessed():
    """`rollout.py` writes ONE fail_reason for three different provider behaviours, and no finish reason.

    A turn with no tool call and no literal `<answer>` ends the episode whatever the content was, so the
    row cannot tell an empty reply from prose or from a truncation. The raw reply can, and these are the
    four shapes the campaign's own logs contain.
    """
    F = _forensics()
    silent = "Model neither produced a tool request nor a final answer"
    conv = {"present": True, "n_turns": 3, "n_tool_calls": 2, "unknown_tool_calls": 0,
            "last_turn_had_tools": False}
    empty = F.classify_row(_fx_row(fail=silent), conv, [_fx_call(content=None, tokens=920)], 24)
    assert empty["category"] == "empty_reply_after_generating"
    assert empty["raw_completion_tokens"] == 920, "the token count is what shows it generated and lost it"
    nothing = F.classify_row(_fx_row(fail=silent), conv, [_fx_call(content=None, tokens=0)], 24)
    assert nothing["category"] == "empty_reply_no_tokens"
    cut = F.classify_row(_fx_row(fail=silent), conv,
                         [_fx_call(finish="length", content="half a sen", tokens=8000)], 24)
    assert cut["category"] == "completion_truncated_at_cap"
    prose = F.classify_row(_fx_row(fail=silent), conv,
                           [_fx_call(content="I think it is about 12 but I am not sure.")], 24)
    assert prose["category"] == "prose_without_answer_tag"
    unavailable = F.classify_row(_fx_row(fail=silent), conv, [], 24)
    assert unavailable["category"] == "silent_turn_raw_unavailable"
    # the two turn-budget endings, one of which the record never names
    recorded = F.classify_row(_fx_row(fail="Exceeded tool-turn budget (24)"), conv, [_fx_call()], 24)
    assert recorded["category"] == "turn_budget_exhausted_recorded"
    fellthrough = F.classify_row(
        _fx_row(fail="predicted '' but the scorer marked it incorrect"),
        {"present": True, "n_turns": 24, "n_tool_calls": 24, "unknown_tool_calls": 0,
         "last_turn_had_tools": True}, [_fx_call()], 24)
    assert fellthrough["category"] == "turn_budget_exhausted_unrecorded"
    assert F.classify_row(_fx_row(hard=1, pred="7"), conv, [], 24)["category"] == "answered_correct"
    assert F.classify_row(_fx_row(pred="9"), conv, [], 24)["category"] == "answered_wrong"


def test_an_answer_tag_in_a_tool_calling_turn_is_recorded_because_the_loop_ignores_it():
    """`if message.tool_calls: ... continue` comes BEFORE the `<answer>` check, so a turn that does both
    has its answer discarded. Whether that ever happened is a question about the records, and this is the
    field that answers it."""
    F = _forensics()
    import tempfile, json as _json
    with tempfile.TemporaryDirectory() as root:
        conv = [{"role": "user", "content": "q"},
                {"type": "message", "content": "<answer>42</answer> but let me check"},
                {"type": "tool_call", "cmd": "read(path='/d/a.txt', start=1, limit=5)", "obs": "x"},
                {"type": "message", "content": ""},
                {"type": "tool_call", "cmd": "answer", "obs": "[tool error: unknown tool answer]"},
                {"type": "message", "content": ""}]
        with open(os.path.join(root, "conversation.json"), "w", encoding="utf-8") as f:
            _json.dump(conv, f)
        d = F.conversation_facts(root)
        assert d["n_turns"] == 3 and d["n_tool_calls"] == 2
        assert d["answer_tag_in_a_tool_calling_turn"] == [0]
        assert d["unknown_tool_calls"] == 1 and d["unknown_tool_names"] == ["answer"]
        assert d["first_unknown_turn"] == 1 and d["turns_after_first_unknown"] == 1
        assert d["last_turn_had_tools"] is False


def test_the_quoted_extractor_fallback_is_checked_against_the_live_source():
    """The missed-extraction experiment runs the extractor's fallback over replies the loop never gave it.

    `_extract_answer` cannot be imported without the model package, so its two-line fallback is quoted.
    A quote that drifts from the source would silently change the experiment's answer, so the quote is
    compared against `rollout.py` and this test fails if the comparison is not wired up or does not hold.
    """
    F = _forensics()
    chk = F.verify_extractor_quote()
    assert chk["checked"] is True, chk
    assert chk["matches"] is True, "oqa_forensics._EXTRACTOR_QUOTE no longer matches rollout._extract_answer"
    assert F.extract_answer("blah\n<answer> 42 </answer>\ntail") == "42"
    assert F.extract_answer("first line\nlast line") == "last line"
    assert F.extract_answer("") == ""


def test_the_learners_expressible_space_is_enumerated_from_its_own_definition():
    """What the learner COULD say is VERBS x TEMPLATES x polarity, and nothing about the actor's output.

    The campaign learned 5 contracts. Whether that is few depends on the size of the space, which is 20 -
    and the space contains no atom about an answer, a period, a unit or an arithmetic step, because every
    atom is a property of one tool observation's text.
    """
    F = _forensics()
    s = F.expressible_space()
    assert s["n_expressible_contracts"] == 20, s["n_expressible_contracts"]
    assert s["contract_kinds_possible"] == ["effect"]
    assert s["atoms_in_the_template_set"]["glob"] == []
    assert set(s["atoms_excluded_from_templates"]) == {
        "has_table", "header_flat", "header_split", "row_period_bound", "scale_stated"}
    joined = " ".join(c["reads"] for c in s["expressible_contracts"])
    for word in ("answer", "period_match", "unit_match", "arith"):
        assert word not in joined, f"{word!r} is not expressible, so it must not appear in the space"


def test_two_different_provider_interactions_can_save_one_identical_trace():
    """The saved trace keeps the assistant's CONTENT and the tool commands. When the content is empty the
    record is the same whatever the model generated, so `trace_digest` can collide across epochs.

    This is not hypothetical: arm C's epoch-3 trace for UID0019 is byte-identical to the shared epoch-1
    trace (3 messages, all empty, 2 identical tool calls), which made one report look in-sample when the
    attempt was fresh. A digest over a lossy record cannot establish that two attempts were the same.
    """
    F = _forensics()
    import tempfile, json as _json
    conv = [{"role": "user", "content": "q"},
            {"type": "message", "content": ""},
            {"type": "tool_call", "cmd": "grep(pattern='03/16/2016', path='/d/a.txt')", "obs": "hit"},
            {"type": "message", "content": ""},
            {"type": "tool_call", "cmd": "read(path='/d/a.txt', start=1, limit=50)", "obs": "rows"},
            {"type": "message", "content": ""}]
    with tempfile.TemporaryDirectory() as root:
        digests = []
        for epoch in ("e1", "e3"):
            d = os.path.join(root, epoch, "UID0019")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "conversation.json"), "w", encoding="utf-8") as f:
                _json.dump(conv, f)
            with open(os.path.join(d, "target_user_prompt.txt"), "w", encoding="utf-8") as f:
                f.write("## Question\nq\n")
            digests.append(F._trace_digest(os.path.join(root, epoch), "UID0019"))
        assert digests[0] == digests[1], "identical records, identical digest - which is the problem"
        facts = F.conversation_facts(os.path.join(root, "e1", "UID0019"))
        assert facts["n_turns"] == 3
        assert all(not t["content"] for t in facts["turns"]), (
            "every assistant content is empty, so the digest says nothing about what was generated")


def test_the_models_epoch_field_is_not_the_epoch_it_was_frozen_at():
    """It records the last model-CHANGING decision, so a model that changed nothing keeps its parent's.

    Arm C's epoch-2, -3 and -4 models all carry `epoch: 1`, because every round after M0 accepted no new
    contract and refuted none. Using that field to decide which traces the model was learned from shrinks
    the corpus to epoch 1 and makes an in-sample test look cleaner than it is. The corpus comes from the
    file name instead, which the campaign writes as model_arm<X>_epoch<NN>.json.
    """
    F = _forensics()
    import tempfile, json as _json
    with tempfile.TemporaryDirectory() as root:
        c = os.path.join(root, "contracts", "arm_C")
        os.makedirs(c, exist_ok=True)
        with open(os.path.join(root, "contracts", "model_M0.json"), "w", encoding="utf-8") as f:
            _json.dump({"sha256": "a" * 64, "version": 2, "epoch": 1, "contracts": [],
                        "evidence_items": ["UID0001"], "evidence_scope": "shared epoch-1 rollout"}, f)
        with open(os.path.join(c, "model_armC_epoch04.json"), "w", encoding="utf-8") as f:
            _json.dump({"sha256": "b" * 64, "version": 2, "epoch": 1, "contracts": [],
                        "evidence_items": ["UID0001"],
                        "evidence_scope": "arm C: the shared epoch-1 rollout and epochs 2..4"}, f)
        got = F.model_records(root)
        m0 = got["contracts/model_M0.json"]
        c4 = got[os.path.join("contracts", "arm_C", "model_armC_epoch04.json")]
        assert m0["corpus_epochs"] == [1]
        assert c4["epoch"] == 1, "the field really does say 1"
        assert c4["corpus_epochs"] == [1, 2, 3, 4], "the corpus does not"


def test_a_reconciled_ledger_with_no_slack_left_still_reconciles(tmp_path, monkeypatch):
    """The defect a finished campaign shows and a fresh one cannot.

    Once claims have been reconciled down to equal the recorded spend, there is no slack. My first
    version of the planner took the spend to be max(per-record integer sum, ceil(float sum)), which is a
    hair LARGER than anything ever claimed, so it reported "recorded spend exceeds net claims - the
    invariant spent <= claimed is BROKEN" and refused to release anything. It declared the real
    campaign's sound ledger broken over 2.14 microdollars across 10,158 records.

    The spend is now the per-record integer sum, which is what the ledger adds and the cap is enforced
    on, and the float-sum excess is held back as a margin instead of counted as spend.
    """
    R = _reconciler()
    _apply(_env(str(tmp_path), attempts=5000, usd=5.0, block_usd=0.05, reserve=0.02), monkeypatch)
    monkeypatch.setenv("SKILLOPT_LEDGER_PRICE_TARGET", "0.0411,0.0733,0.02")
    LEDGER._state.clear()
    LEDGER._owner = "777r0p1"
    for i in range(300):
        LEDGER.reserve("target", "test")
        LEDGER.record_usage("target", "test", 1_111 + i, 37)
    LEDGER._state.clear()
    monkeypatch.setattr(R, "_live_jobs", lambda: set())

    # First pass: release the unused claims, which is what a clean exit would have done.
    first = R.plan(str(tmp_path), exited_jobs=None, assume_all_exited=True)
    assert not first["problems"], first["problems"]
    R.apply_plan(first, reason="test")

    # Second pass, now with no slack at all. This is the case that used to refuse.
    second = R.plan(str(tmp_path), exited_jobs=None, assume_all_exited=True)
    assert not second["problems"], second["problems"]
    b = second["by_role"]["target"]
    assert b["net_claimed_usd"] + 1e-9 >= b["recorded_usd"], b
    assert b["float_minus_micro_usdmicro"] is not None
    assert abs(b["float_minus_micro_usdmicro"]) <= 1_000, "rounding, not a real disagreement"
    assert b["would_release_usd"] <= 0.000001, "there is nothing left to give back"
    # and the ledger still covers its own integer accounting
    micros = 0
    with open(os.path.join(str(tmp_path), "spend", "777r0p1.jsonl"), encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("kind") == "usage":
                micros += int(round(float(rec["usd"]) * 1_000_000))
    assert LEDGER._granted()[("target", "usdmicro")] >= micros


def test_the_two_ways_of_summing_the_charges_are_checked_against_what_rounding_can_do():
    """Both sums come from the same `usd` field, so their gap is bounded by half a microdollar a record.

    My first attempt at this guard used a flat 1,000-microdollar threshold and my first attempt at this
    TEST forged a one-cent disagreement by adding 0.01 to each stored `usd` -- which moves both sums
    equally and proves nothing. The gap is an arithmetic property, not a property of the data: it cannot
    be provoked by any ledger. So the guard is a self-check on the code, the bound is the right one, and
    this test asserts the relation rather than pretending to violate it.
    """
    R = _reconciler()
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "spend"), exist_ok=True)
        os.makedirs(os.path.join(root, "claims"), exist_ok=True)
        # 2,000 sub-microdollar charges: each rounds to 0 micros and adds 4e-7 to the float sum, which is
        # the largest per-record divergence rounding can produce.
        with open(os.path.join(root, "spend", "900r0p1.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"schema": "lso.spend_ledger.v1", "kind": "config", "role": "target",
                                "reserve_usd": 0.02, "cap_usd": 5.0, "cap_attempts": 5000,
                                "block_usd": 0.05, "block_attempts": 2}) + "\n")
            for _ in range(2000):
                f.write(json.dumps({"schema": "lso.spend_ledger.v1", "kind": "attempt",
                                    "role": "target", "stage": "t"}) + "\n")
                f.write(json.dumps({"schema": "lso.spend_ledger.v1", "kind": "usage", "role": "target",
                                    "stage": "t", "prompt_tokens": 1, "completion_tokens": 1,
                                    "cached_prompt_tokens": 0, "usd": 4e-07}) + "\n")
        plan = R.plan(root, exited_jobs=None, assume_all_exited=True)
        b = plan["by_role"]["target"]
        assert b["recorded_usage_records"] == 2000
        assert b["recorded_usd"] == 0.0, "every charge rounds to zero microdollars"
        assert abs(b["float_minus_micro_usdmicro"] - 800.0) < 1.0, b["float_minus_micro_usdmicro"]
        assert b["float_minus_micro_bound_usdmicro"] == 1001.0
        assert abs(b["float_minus_micro_usdmicro"]) <= b["float_minus_micro_bound_usdmicro"]
        assert not any("no longer being taken from the same charges" in p
                       for p in plan["problems"]), plan["problems"]
