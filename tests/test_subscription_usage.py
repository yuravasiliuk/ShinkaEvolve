from __future__ import annotations

import asyncio
import json
import stat
import sys
import time
from pathlib import Path

import pytest

from shinka.core import EvolutionConfig
from shinka.core.async_runner import ShinkaEvolveRunner
from shinka.llm import subscription_usage
from shinka.llm.providers.headless import query_headless
from shinka.llm.subscription_usage import (
    SubscriptionLimitError,
    SubscriptionUsage,
    active_limit_pause,
    clear_limit_hit,
    get_claude_usage,
    get_limit_hit,
    looks_like_limit_error,
    parse_limit_reset_epoch,
    record_limit_hit,
    set_limit_pause_until,
)


@pytest.fixture(autouse=True)
def _clean_state():
    subscription_usage._reset_state_for_tests()
    yield
    subscription_usage._reset_state_for_tests()


def _write_credentials(tmp_path: Path) -> Path:
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "test-token"}}),
        encoding="utf-8",
    )
    return path


def _payload(five_pct=12.0, seven_pct=20.0, five_resets="2026-07-04T03:20:00+00:00"):
    return {
        "five_hour": {"utilization": five_pct, "resets_at": five_resets},
        "seven_day": {
            "utilization": seven_pct,
            "resets_at": "2026-07-08T01:00:00+00:00",
        },
    }


def test_get_claude_usage_parses_payload(tmp_path, monkeypatch):
    creds = _write_credentials(tmp_path)
    monkeypatch.setattr(
        subscription_usage, "_fetch_usage_payload", lambda token: _payload()
    )
    usage = get_claude_usage(cache_ttl=0.0, credentials_path=creds)
    assert usage is not None
    assert usage.five_hour_pct == 12.0
    assert usage.seven_day_pct == 20.0
    assert usage.five_hour_resets_at is not None
    assert usage.seven_day_resets_at is not None


def test_get_claude_usage_caches(tmp_path, monkeypatch):
    creds = _write_credentials(tmp_path)
    calls = {"n": 0}

    def fake_fetch(token):
        calls["n"] += 1
        return _payload()

    monkeypatch.setattr(subscription_usage, "_fetch_usage_payload", fake_fetch)
    first = get_claude_usage(cache_ttl=60.0, credentials_path=creds)
    second = get_claude_usage(cache_ttl=60.0, credentials_path=creds)
    assert first is not None and second is not None
    assert calls["n"] == 1


def test_get_claude_usage_missing_credentials(tmp_path):
    assert get_claude_usage(cache_ttl=0.0, credentials_path=tmp_path / "nope") is None


def test_get_claude_usage_fetch_failure(tmp_path, monkeypatch):
    creds = _write_credentials(tmp_path)
    monkeypatch.setattr(subscription_usage, "_fetch_usage_payload", lambda token: None)
    assert get_claude_usage(cache_ttl=0.0, credentials_path=creds) is None


def test_limit_error_detection():
    assert looks_like_limit_error("Claude AI usage limit reached|1751600000")
    assert looks_like_limit_error("5-hour limit reached ∙ resets 3pm")
    assert looks_like_limit_error("You've hit your usage limit.")
    assert not looks_like_limit_error("Number of request tokens has exceeded rate limit")
    assert not looks_like_limit_error("Some unrelated CLI failure")


def test_parse_limit_reset_epoch():
    assert parse_limit_reset_epoch("Claude AI usage limit reached|1751600000") == (
        1751600000.0
    )
    # milliseconds normalize to seconds
    assert parse_limit_reset_epoch("usage limit reached|1751600000000") == (
        1751600000.0
    )
    assert parse_limit_reset_epoch("limit reached, resets at 3pm") is None


def test_limit_hit_state_flow():
    assert get_limit_hit() is None
    assert active_limit_pause() is None

    record_limit_hit(resets_at=None)
    # unresolved hit counts as active so concurrent calls fail fast
    assert active_limit_pause() == float("inf")

    deadline = time.time() + 60.0
    set_limit_pause_until(deadline)
    assert active_limit_pause() == deadline

    set_limit_pause_until(time.time() - 1.0)
    assert active_limit_pause() is None

    clear_limit_hit()
    assert get_limit_hit() is None


def _fake_limit_cli(tmp_path: Path) -> str:
    script = tmp_path / "fake_limit_headless.py"
    script.write_text(
        "import sys\nif '--check' in sys.argv:\n    raise SystemExit(0)\nsys.stderr.write('Claude AI usage limit reached|1751600000')\nraise SystemExit(1)",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return f"{sys.executable} {script}"


def test_query_headless_limit_error_records_hit(tmp_path, monkeypatch):
    monkeypatch.setenv("SHINKA_HEADLESS_COMMAND", _fake_limit_cli(tmp_path))
    with pytest.raises(SubscriptionLimitError) as excinfo:
        query_headless(
            client=None,
            model="headless/claude",
            msg="hello",
            system_msg="sys",
            msg_history=[],
            output_model=None,
            headless_work_dir=str(tmp_path),
        )
    assert excinfo.value.resets_at == 1751600000.0
    hit = get_limit_hit()
    assert hit is not None
    assert hit.resets_at == 1751600000.0


def test_query_headless_fails_fast_while_paused(tmp_path, monkeypatch):
    # Any CLI spawn would blow up loudly; the call must not get that far.
    monkeypatch.setenv("SHINKA_HEADLESS_COMMAND", "/nonexistent-headless-cmd")
    record_limit_hit(resets_at=None)
    set_limit_pause_until(time.time() + 3600.0)
    with pytest.raises(SubscriptionLimitError):
        query_headless(
            client=None,
            model="headless/claude",
            msg="hello",
            system_msg="sys",
            msg_history=[],
            output_model=None,
            headless_work_dir=str(tmp_path),
        )


def test_non_claude_agents_ignore_pause(tmp_path, monkeypatch):
    record_limit_hit(resets_at=None)
    set_limit_pause_until(time.time() + 3600.0)
    monkeypatch.setenv("SHINKA_HEADLESS_COMMAND", "/nonexistent-headless-cmd")
    # codex is unaffected by a claude pause; it should reach the (missing)
    # CLI and fail with a non-subscription error.
    with pytest.raises(Exception) as excinfo:
        query_headless(
            client=None,
            model="headless/codex",
            msg="hello",
            system_msg="sys",
            msg_history=[],
            output_model=None,
            headless_work_dir=str(tmp_path),
        )
    assert not isinstance(excinfo.value, SubscriptionLimitError)


class _StubRunner:
    """Bare-attribute stand-in to drive the unbound runner gating methods."""

    _subscription_pause_active = ShinkaEvolveRunner._subscription_pause_active
    _subscription_threshold_pct = ShinkaEvolveRunner._subscription_threshold_pct
    _format_epoch = staticmethod(ShinkaEvolveRunner._format_epoch)

    def __init__(self, threshold=0.95, poll_interval=0.0):
        self.evo_config = EvolutionConfig(
            subscription_pause_threshold=threshold,
            subscription_usage_poll_interval=poll_interval,
        )
        self.subscription_pause_until = None
        self.subscription_limit_stop = False


def _usage(five_pct, seven_pct=10.0, five_resets_in=120.0):
    now = time.time()
    return SubscriptionUsage(
        five_hour_pct=five_pct,
        five_hour_resets_at=now + five_resets_in,
        seven_day_pct=seven_pct,
        seven_day_resets_at=now + 3 * 86400,
        fetched_at=now,
    )


def _run_pause_check(stub, monkeypatch, usage):
    monkeypatch.setattr(
        subscription_usage, "get_claude_usage", lambda cache_ttl: usage
    )
    return asyncio.run(_StubRunner._subscription_pause_active(stub))


def test_runner_gate_below_threshold(monkeypatch):
    stub = _StubRunner()
    assert _run_pause_check(stub, monkeypatch, _usage(five_pct=40.0)) is False
    assert stub.subscription_pause_until is None


def test_runner_gate_five_hour_threshold_pauses(monkeypatch):
    stub = _StubRunner()
    assert _run_pause_check(stub, monkeypatch, _usage(five_pct=96.0)) is True
    assert stub.subscription_pause_until is not None
    assert stub.subscription_pause_until > time.time()
    # threshold pause must NOT arm the provider fail-fast (in-flight work
    # at 96% still succeeds and should finish)
    assert active_limit_pause() is None


def test_runner_gate_weekly_threshold_stops(monkeypatch):
    stub = _StubRunner()
    assert (
        _run_pause_check(stub, monkeypatch, _usage(five_pct=10.0, seven_pct=97.0))
        is True
    )
    assert stub.subscription_limit_stop is True


def test_runner_gate_reactive_hit_arms_provider_pause(monkeypatch):
    stub = _StubRunner()
    resets_at = time.time() + 600.0
    record_limit_hit(resets_at=resets_at)
    assert _run_pause_check(stub, monkeypatch, _usage(five_pct=50.0)) is True
    # runner resolved the deadline and armed the provider fail-fast
    assert active_limit_pause() == pytest.approx(resets_at + 30.0)


def test_runner_gate_resumes_after_pause_expiry(monkeypatch):
    stub = _StubRunner()
    stub.subscription_pause_until = time.time() - 5.0
    record_limit_hit(resets_at=None)
    set_limit_pause_until(time.time() - 5.0)
    assert _run_pause_check(stub, monkeypatch, _usage(five_pct=10.0)) is False
    assert stub.subscription_pause_until is None
    assert get_limit_hit() is None


def test_runner_gate_disabled_without_usage_data(monkeypatch):
    stub = _StubRunner()
    assert _run_pause_check(stub, monkeypatch, None) is False


def test_runner_gate_percent_style_threshold(monkeypatch):
    stub = _StubRunner(threshold=95)  # percent form
    assert _run_pause_check(stub, monkeypatch, _usage(five_pct=96.0)) is True


def test_evolution_config_defaults():
    config = EvolutionConfig()
    assert config.subscription_pause_threshold == 0.95
    assert config.subscription_usage_poll_interval == 60.0
