from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_bridge.adapters.fake import FakeAdapter
from agent_bridge.models import (
    TERMINAL_STATUSES,
    ProcState,
    Session,
    Task,
    TaskStatus,
    TurnResult,
    iso,
)
from agent_bridge.paths import result_path, state_path, transcript_path
from agent_bridge.persist import atomic_write_json, read_json
from agent_bridge.registry import (
    NESTED_CANCEL_ERROR,
    NESTED_DISPATCH_ERROR,
    NESTED_END_SESSION_ERROR,
    NESTED_PREFERENCES_ERROR,
    Registry,
)
from agent_bridge.transcript import append_event, read_events


@pytest.mark.asyncio
async def test_dispatch_records_model_and_effort(bridge_home, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        dispatched = await registry.dispatch_task(
            "fake",
            "tiny",
            cwd=str(work.resolve()),
            model="gemini-3.7-flash",
            effort="LOW",
        )
        assert dispatched["model"] == "gemini-3.7-flash"
        assert dispatched["effort"] == "low"
        session = registry.sessions[dispatched["session_id"]]
        assert session.model == "gemini-3.7-flash"
        assert session.effort == "low"
        with pytest.raises(ValueError, match="effort"):
            await registry.dispatch_task("fake", "bad", cwd=str(work.resolve()), effort="turbo")
    finally:
        await registry.stop()


def test_session_scope_is_explicit(bridge_home, monkeypatch):
    registry = Registry.create(bridge_home)
    monkeypatch.setattr("agent_bridge.registry.count_sibling_servers", lambda: 3)
    assert registry.session_scope() == {
        "scope": "current_instance",
        "other_live_instances": 3,
    }


@pytest.mark.asyncio
async def test_dispatch_wait_fake(bridge_home, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("fake", "build foo", cwd=str(work.resolve()), title="demo")
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert waited["status"] == "completed"
        assert "build foo" in waited["result_text"]
        sessions = registry.list_sessions()
        assert sessions[0]["title"] == "demo"
        assert registry.sessions[dispatched["session_id"]].proc_state == ProcState.ready
        transcript = registry.get_transcript(dispatched["session_id"])
        assert transcript["count"] >= 1
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_busy_session_rejected(bridge_home, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_FAKE_DELAY", "2")
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        first = await registry.dispatch_task("fake", "slow", cwd=str(work.resolve()))
        with pytest.raises(RuntimeError, match="busy"):
            await registry.dispatch_task(
                "fake",
                "again",
                cwd=str(work.resolve()),
                session_id=first["session_id"],
            )
        await registry.cancel_task(first["task_id"])
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_followup_cwd_must_match(bridge_home, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        first = await registry.dispatch_task("fake", "one", cwd=str(work.resolve()))
        await registry.wait_task(first["task_id"], timeout_sec=5)
        with pytest.raises(ValueError, match="same project folder"):
            await registry.dispatch_task(
                "fake",
                "two",
                cwd=str(other.resolve()),
                session_id=first["session_id"],
            )
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_relative_cwd_rejected(bridge_home):
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        with pytest.raises(ValueError, match="absolute"):
            await registry.dispatch_task("fake", "x", cwd="relative")
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_dispatch_rejects_missing_cwd_and_file_path(bridge_home, tmp_path):
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        with pytest.raises(ValueError, match="does not exist"):
            await registry.dispatch_task("fake", "x", cwd=str(tmp_path / "missing"))
        file_path = tmp_path / "file.txt"
        file_path.write_text("x", encoding="utf-8")
        with pytest.raises(ValueError, match="directory"):
            await registry.dispatch_task("fake", "x", cwd=str(file_path))
        assert registry.sessions == {}
        assert registry.tasks == {}
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_bridge_restart_marks_running_failed(bridge_home):
    atomic_write_json(
        state_path(bridge_home),
        {
            "sessions": [
                Session(
                    session_id="sess_old",
                    agent="fake",
                    cwd=str(Path.cwd()),
                    proc_state=ProcState.busy,
                ).model_dump(mode="json")
            ],
            "tasks": [
                Task(
                    task_id="task_old",
                    session_id="sess_old",
                    agent="fake",
                    message="in flight",
                    cwd=str(Path.cwd()),
                    status=TaskStatus.running,
                ).model_dump(mode="json")
            ],
        },
    )
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        assert registry.tasks["task_old"].status == TaskStatus.failed
        assert registry.tasks["task_old"].error == "bridge_restarted"
        assert registry.sessions["sess_old"].proc_state == ProcState.idle_unloaded
        assert registry.sessions["sess_old"].owner_pid == registry._owner_pid
        assert registry.sessions["sess_old"].owner_create_time == registry._owner_create_time
        assert registry.tasks["task_old"].owner_pid == registry._owner_pid
        assert registry.tasks["task_old"].owner_create_time == registry._owner_create_time
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_sibling_instances_do_not_clobber_state(bridge_home, tmp_path, monkeypatch):
    monkeypatch.setattr("agent_bridge.registry.owner_alive", lambda pid, create_time: True)
    work = tmp_path / "work"
    work.mkdir()
    cwd = str(work.resolve())
    a = Registry.create(bridge_home, owner_pid=1001, owner_create_time=11.0)
    b = Registry.create(bridge_home, owner_pid=2002, owner_create_time=22.0)
    await a.start()
    try:
        dispatched = await a.dispatch_task("fake", "tiny", cwd=cwd)
        waited = await a.wait_task(dispatched["task_id"], timeout_sec=5)
        assert waited["status"] == "completed"
        sess_a = dispatched["session_id"]
        task_a = dispatched["task_id"]
        assert a.sessions[sess_a].owner_pid == 1001
        assert a.tasks[task_a].owner_pid == 1001

        await b.start()
        try:
            assert sess_a not in b.sessions
            assert task_a not in b.tasks
            b.save()
            await b.flush_state()
            payload = read_json(state_path(bridge_home), {})
            assert any(row["session_id"] == sess_a for row in payload["sessions"])
            assert any(row["task_id"] == task_a for row in payload["tasks"])

            other = await b.dispatch_task("fake", "other", cwd=cwd)
            await b.flush_state()
            payload = read_json(state_path(bridge_home), {})
            session_ids = {row["session_id"] for row in payload["sessions"]}
            assert {sess_a, other["session_id"]} <= session_ids
            assert any(row["task_id"] == task_a for row in payload["tasks"])

            a.save()
            await a.flush_state()
            payload = read_json(state_path(bridge_home), {})
            session_ids = {row["session_id"] for row in payload["sessions"]}
            assert {sess_a, other["session_id"]} <= session_ids
            assert any(row["task_id"] == task_a for row in payload["tasks"])
            assert any(row["task_id"] == other["task_id"] for row in payload["tasks"])
        finally:
            await b.stop()
    finally:
        await a.stop()


@pytest.mark.asyncio
async def test_dead_owner_records_are_adopted(bridge_home, monkeypatch):
    monkeypatch.setattr("agent_bridge.registry.owner_alive", lambda pid, create_time: False)
    cwd = str(Path.cwd())
    atomic_write_json(
        state_path(bridge_home),
        {
            "sessions": [
                Session(
                    session_id="sess_dead",
                    agent="fake",
                    cwd=cwd,
                    proc_state=ProcState.busy,
                    owner_pid=9999,
                    owner_create_time=1.0,
                ).model_dump(mode="json")
            ],
            "tasks": [
                Task(
                    task_id="task_dead",
                    session_id="sess_dead",
                    agent="fake",
                    message="in flight",
                    cwd=cwd,
                    status=TaskStatus.running,
                    owner_pid=9999,
                    owner_create_time=1.0,
                ).model_dump(mode="json")
            ],
        },
    )
    registry = Registry.create(bridge_home, owner_pid=2002, owner_create_time=22.0)
    await registry.start()
    try:
        task = registry.tasks["task_dead"]
        assert task.status == TaskStatus.failed
        assert task.error == "bridge_restarted"
        assert task.finished_at is not None
        assert task.owner_pid == 2002
        assert task.owner_create_time == 22.0
        session = registry.sessions["sess_dead"]
        assert session.proc_state == ProcState.idle_unloaded
        assert session.owner_pid == 2002
        assert session.owner_create_time == 22.0
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_legacy_records_without_owner_fields_are_adopted(bridge_home):
    cwd = str(Path.cwd())
    atomic_write_json(
        state_path(bridge_home),
        {
            "sessions": [
                {
                    "session_id": "sess_legacy",
                    "agent": "fake",
                    "cwd": cwd,
                    "proc_state": "busy",
                }
            ],
            "tasks": [
                {
                    "task_id": "task_legacy",
                    "session_id": "sess_legacy",
                    "agent": "fake",
                    "message": "in flight",
                    "cwd": cwd,
                    "status": "running",
                }
            ],
        },
    )
    registry = Registry.create(bridge_home, owner_pid=3003, owner_create_time=33.0)
    await registry.start()
    try:
        task = registry.tasks["task_legacy"]
        assert task.status == TaskStatus.failed
        assert task.error == "bridge_restarted"
        assert task.owner_pid == 3003
        assert task.owner_create_time == 33.0
        session = registry.sessions["sess_legacy"]
        assert session.proc_state == ProcState.idle_unloaded
        assert session.owner_pid == 3003
        assert session.owner_create_time == 33.0
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_get_result_includes_workspace_writes(bridge_home, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    result_text = "结" * 8_605

    async def write_then_ok(self, session, task):
        (Path(task.cwd) / "smoke.txt").write_text("hello-bridge\n", encoding="utf-8")
        hidden = Path(task.cwd) / ".sessions"
        hidden.mkdir()
        (hidden / "log.jsonl").write_text("{}\n", encoding="utf-8")
        return TurnResult(text=result_text, files_changed=[])

    monkeypatch.setattr(FakeAdapter, "run_turn", write_then_ok)
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        dispatched = await registry.dispatch_task(
            "fake",
            "write smoke",
            cwd=str(work.resolve()),
            model="gemini-3.7-flash",
            effort="low",
        )
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert waited["status"] == "completed"
        assert waited["files_changed"] == ["smoke.txt"]
        assert waited["model"] == "gemini-3.7-flash"
        assert waited["effort"] == "low"
        assert waited["observed_model"] is None
        result = registry.get_result(dispatched["task_id"])
        assert result["model"] == "gemini-3.7-flash"
        assert "observed_model" in result
        assert result["result_text"] == result_text
        assert result["has_more"] is False
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_get_result_reads_complete_unicode_result_in_pages(
    bridge_home, tmp_path, monkeypatch
):
    work = tmp_path / "work"
    work.mkdir()
    full_text = "汉🙂abc\n" * 25_000

    async def long_turn(self, session, task):
        return TurnResult(text=full_text)

    monkeypatch.setattr(FakeAdapter, "run_turn", long_turn)
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("fake", "long", cwd=str(work.resolve()))
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert waited["status"] == "completed"
        assert waited["result_truncated"] is True
        assert waited["result_total_chars"] == len(full_text)
        assert len(registry.tasks[dispatched["task_id"]].result_text) < len(full_text)
        assert result_path(dispatched["task_id"], bridge_home).read_text(
            encoding="utf-8"
        ) == full_text

        cursor = 0
        parts: list[str] = []
        while True:
            page = registry.get_result(
                dispatched["task_id"], cursor=cursor, max_chars=17_000
            )
            assert page["result_complete"] is True
            assert page["result_source"] == "artifact"
            assert page["result_total_chars"] == len(full_text)
            parts.append(page["result_text"])
            if not page["has_more"]:
                assert page["next_cursor"] is None
                break
            cursor = page["next_cursor"]
        assert "".join(parts) == full_text
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_task_lifecycle_logging_is_sparse(bridge_home, tmp_path, caplog):
    work = tmp_path / "work"
    work.mkdir()
    caplog.set_level(logging.INFO, logger="agent_bridge.registry")
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        dispatched = await registry.dispatch_task(
            "fake", "do not log this prompt", cwd=str(work.resolve())
        )
        await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        messages = [record.getMessage() for record in caplog.records]
        lifecycle = [message for message in messages if message.startswith("task_")]
        assert len([message for message in lifecycle if message.startswith("task_dispatched")]) == 1
        assert len([message for message in lifecycle if message.startswith("task_finished")]) == 1
        assert all("do not log this prompt" not in message for message in lifecycle)
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_kimi_silent_failure_becomes_a_warning(bridge_home, tmp_path, monkeypatch):
    """Kimi answers end_turn on a failed turn; wire.jsonl is the only witness."""
    work = tmp_path / "work"
    work.mkdir()
    bridge_home.mkdir(parents=True, exist_ok=True)
    # Run the real kimi branch in the registry against a fake transport.
    (bridge_home / "agents.toml").write_text(
        '[agents.kimi]\nprotocol = "fake"\n', encoding="utf-8"
    )
    monkeypatch.setattr(
        "agent_bridge.registry.observe_kimi_session",
        lambda native_id: {
            "model": "kimi-code/k3-256k",
            "effort": "high",
            "failure": "failed: provider.api_error: 402 membership",
        },
    )

    async def empty_turn(self, session, task):
        return TurnResult(text="", stop_reason="end_turn")

    monkeypatch.setattr(FakeAdapter, "run_turn", empty_turn)
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("kimi", "do it", cwd=str(work.resolve()))
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert waited["status"] == "completed"
        assert waited["stop_reason"] == "end_turn"
        assert waited["observed_model"] == "kimi-code/k3-256k"
        assert waited["observed_effort"] == "high"
        assert any("402 membership" in w for w in waited["warnings"])
        result = registry.get_result(dispatched["task_id"])
        assert "end_turn with empty text" in result["hint"]
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_kimi_clean_turn_adds_no_warning(bridge_home, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    bridge_home.mkdir(parents=True, exist_ok=True)
    (bridge_home / "agents.toml").write_text(
        '[agents.kimi]\nprotocol = "fake"\n', encoding="utf-8"
    )
    monkeypatch.setattr(
        "agent_bridge.registry.observe_kimi_session",
        lambda native_id: {"model": "kimi-code/k3", "effort": "low", "failure": None},
    )
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("kimi", "do it", cwd=str(work.resolve()))
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert waited["status"] == "completed"
        assert waited["warnings"] == []
        assert waited["observed_model"] == "kimi-code/k3"
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_opencode_observed_model_comes_from_the_adapter(bridge_home, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    bridge_home.mkdir(parents=True, exist_ok=True)
    (bridge_home / "agents.toml").write_text(
        '[agents.opencode]\nprotocol = "fake"\n', encoding="utf-8"
    )

    async def applied_turn(self, session, task):
        return TurnResult(
            text="done",
            stop_reason="end_turn",
            observed_model="opencode/x-preview-f-free",
            observed_effort="high",
        )

    monkeypatch.setattr(FakeAdapter, "run_turn", applied_turn)
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        dispatched = await registry.dispatch_task(
            "opencode",
            "do it",
            cwd=str(work.resolve()),
            model="opencode/x-preview-f-free",
            effort="max",
        )
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert waited["status"] == "completed"
        assert waited["model"] == "opencode/x-preview-f-free"
        assert waited["effort"] == "max"
        assert waited["observed_model"] == "opencode/x-preview-f-free"
        assert waited["observed_effort"] == "high"
        result = registry.get_result(dispatched["task_id"])
        assert "last values Bridge successfully set" in result["hint"]
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_claude_observed_model_comes_from_the_adapter(bridge_home, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    bridge_home.mkdir(parents=True, exist_ok=True)
    (bridge_home / "agents.toml").write_text(
        '[agents.claude]\nprotocol = "fake"\n', encoding="utf-8"
    )

    async def applied_turn(self, session, task):
        return TurnResult(
            text="done",
            stop_reason="end_turn",
            observed_model="sonnet",
            observed_effort="xhigh",
        )

    monkeypatch.setattr(FakeAdapter, "run_turn", applied_turn)
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        dispatched = await registry.dispatch_task(
            "claude",
            "do it",
            cwd=str(work.resolve()),
            model="sonnet",
            effort="max",
        )
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert waited["status"] == "completed"
        assert waited["model"] == "sonnet"
        assert waited["effort"] == "max"
        assert waited["observed_model"] == "sonnet"
        assert waited["observed_effort"] == "xhigh"
        result = registry.get_result(dispatched["task_id"])
        assert "Claude Code observed_model/effort" in result["hint"]
        assert "last values Bridge successfully set" in result["hint"]
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_late_turn_result_does_not_overwrite_cancelled(bridge_home, tmp_path, monkeypatch):
    """cancel_task's timeout path finalizes the task; the turn ending later must not flip it back."""
    work = tmp_path / "work"
    work.mkdir()

    async def finalize_then_finish(self, session, task):
        # Simulate cancel_task's 15s-timeout path having already finalized.
        task.status = TaskStatus.cancelled
        task.stop_reason = "cancelled"
        task.finished_at = iso()
        return TurnResult(text="late result", stop_reason="end_turn")

    monkeypatch.setattr(FakeAdapter, "run_turn", finalize_then_finish)
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("fake", "slow", cwd=str(work.resolve()))
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert waited["status"] == "cancelled"
        assert waited["stop_reason"] == "cancelled"
        assert "late result" in waited["result_text"]
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_old_terminal_tasks_are_pruned(bridge_home, tmp_path, monkeypatch):
    monkeypatch.setattr("agent_bridge.registry.TASK_KEEP_PER_SESSION", 2)
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        first = await registry.dispatch_task("fake", "t0", cwd=str(work.resolve()))
        await registry.wait_task(first["task_id"], timeout_sec=5)
        task_ids = [first["task_id"]]
        for index in range(3):
            more = await registry.dispatch_task(
                "fake",
                f"t{index + 1}",
                cwd=str(work.resolve()),
                session_id=first["session_id"],
            )
            await registry.wait_task(more["task_id"], timeout_sec=5)
            task_ids.append(more["task_id"])
        assert task_ids[0] not in registry.tasks
        assert task_ids[-1] in registry.tasks
        assert not result_path(task_ids[0], bridge_home).exists()
        assert result_path(task_ids[-1], bridge_home).is_file()
        terminal = [t for t in registry.tasks.values() if t.session_id == first["session_id"]]
        assert len(terminal) <= 3  # 2 kept terminal + possibly the newest
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_idle_exit_due_predicate(bridge_home, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        assert registry.idle_exit_due() is False

        registry._last_activity = time.monotonic() - registry.config.server.idle_exit_sec - 1
        assert registry.idle_exit_due() is True

        registry.tasks["task_busy"] = Task(
            task_id="task_busy",
            session_id="sess_busy",
            agent="fake",
            message="hold",
            cwd=str(work.resolve()),
            status=TaskStatus.running,
        )
        assert registry.idle_exit_due() is False

        registry.tasks["task_busy"].status = TaskStatus.queued
        assert registry.idle_exit_due() is False

        registry.tasks["task_busy"].status = TaskStatus.completed
        assert registry.idle_exit_due() is True

        registry.config.server.idle_exit_sec = 0
        assert registry.idle_exit_due() is False
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_coordinator_status_default_auto_never_blocks(bridge_home, tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_MODE", raising=False)
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        status = registry.coordinator_status()
        assert status["mode"] == "auto"
        assert status["instructions"] is None
        assert status["hint"]
        assert status["runtime_context"] == "coordinator"
        assert status["dispatch_enabled"] is True
        dispatched = await registry.dispatch_task("fake", "hi", cwd=str(work.resolve()))
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert waited["status"] == "completed"
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_set_preferences_applies_now_and_persists(bridge_home, tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_MODE", raising=False)
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        result = registry.set_preferences(mode="safe", instructions="Coding goes to grok.")
        assert result["coordinator"]["mode"] == "manual"
        assert result["coordinator"]["instructions"] == "Coding goes to grok."
        assert result["notes"]
        # immediate effect in the running instance: manual gate is live
        with pytest.raises(RuntimeError, match="manual"):
            await registry.dispatch_task("fake", "hi", cwd=str(work.resolve()))
        with pytest.raises(ValueError, match="unknown coordinator mode"):
            registry.set_preferences(mode="turbo")
    finally:
        await registry.stop()

    # a fresh instance over the same home reads the persisted overlay
    fresh = Registry.create(bridge_home)
    assert fresh.coordinator_status()["mode"] == "manual"
    assert fresh.coordinator_status()["instructions"] == "Coding goes to grok."


@pytest.mark.asyncio
async def test_manual_mode_blocks_dispatch_without_user_request(bridge_home, tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_MODE", raising=False)
    bridge_home.mkdir(parents=True, exist_ok=True)
    (bridge_home / "agents.toml").write_text(
        """
[coordinator]
mode = "manual"
instructions = "Coding goes to grok."
""",
        encoding="utf-8",
    )
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        status = registry.coordinator_status()
        assert status["mode"] == "manual"
        assert "user_requested" in status["hint"]
        assert status["instructions"] == "Coding goes to grok."
        with pytest.raises(RuntimeError, match="manual"):
            await registry.dispatch_task("fake", "hi", cwd=str(work.resolve()))
        dispatched = await registry.dispatch_task(
            "fake", "hi", cwd=str(work.resolve()), user_requested=True
        )
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert waited["status"] == "completed"
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_worker_status_disables_dispatch(bridge_home):
    registry = Registry.create(bridge_home, runtime_context="worker")
    status = registry.coordinator_status()
    assert status["runtime_context"] == "worker"
    assert status["dispatch_enabled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["manual", "auto", "eager"])
@pytest.mark.parametrize("user_requested", [False, True])
async def test_worker_context_blocks_dispatch_before_validation(
    bridge_home, monkeypatch, mode, user_requested
):
    monkeypatch.delenv("AGENT_BRIDGE_MODE", raising=False)
    bridge_home.mkdir(parents=True, exist_ok=True)
    (bridge_home / "agents.toml").write_text(
        f'[coordinator]\nmode = "{mode}"\n',
        encoding="utf-8",
    )
    registry = Registry.create(bridge_home, runtime_context="worker")
    with pytest.raises(RuntimeError, match="nested dispatch is disabled") as exc:
        await registry.dispatch_task(
            "not-an-agent",
            "x",
            cwd="relative",
            user_requested=user_requested,
        )
    assert NESTED_DISPATCH_ERROR in str(exc.value)


def test_worker_context_set_preferences_does_not_write(bridge_home):
    bridge_home.mkdir(parents=True, exist_ok=True)
    path = bridge_home / "agents.toml"
    original = "[coordinator]\nmode = \"auto\"\ninstructions = \"keep\"\n"
    path.write_text(original, encoding="utf-8")
    mtime = path.stat().st_mtime
    registry = Registry.create(bridge_home, runtime_context="worker")
    with pytest.raises(RuntimeError, match="preference updates are disabled") as exc:
        registry.set_preferences(mode="eager", instructions="changed")
    assert NESTED_PREFERENCES_ERROR in str(exc.value)
    assert path.read_text(encoding="utf-8") == original
    assert path.stat().st_mtime == mtime


def test_registry_detects_worker_context_from_env(bridge_home, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_PARENT_CONTEXT", "worker")
    registry = Registry.create(bridge_home)
    assert registry.runtime_context == "worker"
    assert registry.dispatch_enabled is False


@pytest.mark.asyncio
async def test_worker_context_blocks_cancel_and_end_before_lookup(bridge_home):
    registry = Registry.create(bridge_home, runtime_context="worker")
    with pytest.raises(RuntimeError, match="task cancellation is disabled") as cancel_exc:
        await registry.cancel_task("task_missing")
    assert NESTED_CANCEL_ERROR in str(cancel_exc.value)
    with pytest.raises(RuntimeError, match="session shutdown is disabled") as end_exc:
        await registry.end_session("sess_missing")
    assert NESTED_END_SESSION_ERROR in str(end_exc.value)


def test_registry_injection_overrides_env(bridge_home, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_PARENT_CONTEXT", "worker")
    registry = Registry.create(bridge_home, runtime_context="coordinator")
    assert registry.runtime_context == "coordinator"
    assert registry.dispatch_enabled is True


@pytest.mark.asyncio
async def test_env_status_does_not_block_event_loop(bridge_home, monkeypatch):
    def slow_count() -> int:
        time.sleep(0.5)
        return 3

    monkeypatch.setattr("agent_bridge.registry.count_sibling_servers", slow_count)
    ticks = 0

    async def counter() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.02)

    registry = Registry.create(bridge_home)
    task = asyncio.create_task(counter())
    try:
        status = await registry.env_status()
        assert ticks >= 10
        warnings = status.get("warnings") or []
        assert any("3 other agent-bridge server instance(s)" in item for item in warnings)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_oneshot_adapter_marks_session_idle_unloaded(bridge_home, tmp_path, monkeypatch):
    monkeypatch.setattr(FakeAdapter, "resident", False)
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        first = await registry.dispatch_task("fake", "first turn", cwd=str(work.resolve()))
        waited = await registry.wait_task(first["task_id"], timeout_sec=5)
        assert waited["status"] == "completed"
        session = registry.sessions[first["session_id"]]
        assert session.proc_state == ProcState.idle_unloaded
        assert first["session_id"] not in {
            row["session_id"] for row in registry.list_sessions(active_only=True)
        }
        assert first["session_id"] in {row["session_id"] for row in registry.list_sessions()}
        second = await registry.dispatch_task(
            "fake",
            "second turn",
            cwd=str(work.resolve()),
            session_id=first["session_id"],
        )
        second_wait = await registry.wait_task(second["task_id"], timeout_sec=5)
        assert second_wait["status"] == "completed"
        assert second["session_id"] == first["session_id"]
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_env_status_caches_sibling_count(bridge_home, monkeypatch):
    calls = 0

    def counted() -> int:
        nonlocal calls
        calls += 1
        return 1

    monkeypatch.setattr("agent_bridge.registry.count_sibling_servers", counted)
    registry = Registry.create(bridge_home)
    first = await registry.env_status()
    second = await registry.env_status()
    assert calls == 1
    assert first.get("warnings") == second.get("warnings")


@pytest.mark.asyncio
async def test_workspace_snapshot_runs_off_loop(bridge_home, tmp_path, monkeypatch):
    def slow_snapshot(cwd: str | Path) -> dict[str, tuple[int, int]]:
        time.sleep(0.3)
        return {}

    monkeypatch.setattr("agent_bridge.registry.snapshot_workspace", slow_snapshot)
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    ticks = 0

    async def counter() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.02)

    ticker = asyncio.create_task(counter())
    try:
        dispatched = await registry.dispatch_task("fake", "snap", cwd=str(work.resolve()))
        await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert ticks >= 10
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
        await registry.stop()


@pytest.mark.asyncio
async def test_files_changed_is_capped(bridge_home, tmp_path, monkeypatch):
    paths = [f"gen/{i:04d}.txt" for i in range(500)]
    monkeypatch.setattr("agent_bridge.registry.merge_files_changed", lambda *args, **kwargs: list(paths))
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("fake", "many files", cwd=str(work.resolve()))
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert len(waited["files_changed"]) == 200
        assert waited["files_changed"] == paths[:200]
        assert waited["files_changed_total"] == 500
        assert waited["files_changed_truncated"] is True
        result = registry.get_result(dispatched["task_id"])
        assert "first 200 of 500" in result["hint"]
        checked = registry.check_task(dispatched["task_id"])
        assert checked["files_changed_total"] == 500
        assert checked["files_changed_truncated"] is True
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_files_changed_uncapped_when_under_limit(bridge_home, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("fake", "tiny", cwd=str(work.resolve()))
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert waited["files_changed_truncated"] is False
        assert waited["files_changed_total"] == len(waited["files_changed"])
    finally:
        await registry.stop()


def _dead_session(session_id: str, cwd: str, last_active_at: str, proc_state: ProcState = ProcState.dead) -> Session:
    return Session(
        session_id=session_id,
        agent="fake",
        cwd=cwd,
        proc_state=proc_state,
        last_active_at=last_active_at,
    )


def _completed_task(task_id: str, session_id: str, cwd: str, created_at: str) -> Task:
    return Task(
        task_id=task_id,
        session_id=session_id,
        agent="fake",
        message="x",
        cwd=cwd,
        status=TaskStatus.completed,
        created_at=created_at,
    )


def test_prune_sessions_by_count(bridge_home, tmp_path):
    cwd = str(tmp_path)
    registry = Registry.create(bridge_home)
    base = datetime.now(UTC)
    for index in range(60):
        session_id = f"sess_{index:03d}"
        task_id = f"task_{index:03d}"
        stamp = iso(base + timedelta(seconds=index))
        registry.sessions[session_id] = _dead_session(session_id, cwd, stamp)
        registry.tasks[task_id] = _completed_task(task_id, session_id, cwd, stamp)
        registry._done[task_id] = asyncio.Event()
        result_path(task_id, bridge_home).write_text("ok\n", encoding="utf-8")
    registry._prune()
    kept = {f"sess_{index:03d}" for index in range(10, 60)}
    assert set(registry.sessions) == kept
    for index in range(10):
        assert f"task_{index:03d}" not in registry.tasks
        assert f"task_{index:03d}" not in registry._done
        assert not result_path(f"task_{index:03d}", bridge_home).exists()
    for index in range(10, 60):
        assert f"task_{index:03d}" in registry.tasks
        assert result_path(f"task_{index:03d}", bridge_home).is_file()


def test_prune_sessions_by_age(bridge_home, tmp_path):
    cwd = str(tmp_path)
    registry = Registry.create(bridge_home)
    now = datetime.now(UTC)
    registry.sessions["sess_old"] = _dead_session(
        "sess_old", cwd, iso(now - timedelta(days=20)), ProcState.idle_unloaded
    )
    registry.sessions["sess_mid"] = _dead_session(
        "sess_mid", cwd, iso(now - timedelta(days=2)), ProcState.idle_unloaded
    )
    registry.sessions["sess_new"] = _dead_session(
        "sess_new", cwd, iso(now), ProcState.idle_unloaded
    )
    registry._prune()
    assert "sess_old" not in registry.sessions
    assert {"sess_mid", "sess_new"} <= set(registry.sessions)


def test_prune_never_touches_active(bridge_home, tmp_path):
    cwd = str(tmp_path)
    registry = Registry.create(bridge_home)
    old = iso(datetime.now(UTC) - timedelta(days=20))
    for session_id, state in (
        ("sess_busy", ProcState.busy),
        ("sess_ready", ProcState.ready),
        ("sess_spawning", ProcState.spawning),
    ):
        registry.sessions[session_id] = _dead_session(session_id, cwd, old, state)
    held = _dead_session("sess_held", cwd, old, ProcState.idle_unloaded)
    registry.sessions["sess_held"] = held
    registry._adapters["sess_held"] = FakeAdapter(registry.config.get("fake"), registry.home)
    queued = _dead_session("sess_queued", cwd, old, ProcState.idle_unloaded)
    registry.sessions["sess_queued"] = queued
    registry.tasks["task_queued"] = Task(
        task_id="task_queued",
        session_id="sess_queued",
        agent="fake",
        message="x",
        cwd=cwd,
        status=TaskStatus.queued,
        created_at=old,
    )
    registry._prune()
    assert set(registry.sessions) >= {"sess_busy", "sess_ready", "sess_spawning", "sess_held", "sess_queued"}
    assert "task_queued" in registry.tasks


def test_prune_tasks_global_cap(bridge_home, tmp_path):
    cwd = str(tmp_path)
    registry = Registry.create(bridge_home)
    base = datetime.now(UTC)
    index = 0
    for session_n in range(15):
        session_id = f"sess_{session_n:02d}"
        registry.sessions[session_id] = _dead_session(session_id, cwd, iso(base))
        for _ in range(20):
            task_id = f"task_{index:03d}"
            registry.tasks[task_id] = _completed_task(
                task_id, session_id, cwd, iso(base + timedelta(seconds=index))
            )
            index += 1
    registry._prune()
    assert len(registry.tasks) <= 200
    assert "task_000" not in registry.tasks
    assert "task_299" in registry.tasks
    remaining = sorted(registry.tasks)
    assert remaining == [f"task_{n:03d}" for n in range(100, 300)]


@pytest.mark.asyncio
async def test_start_prunes_stale_sessions(bridge_home, tmp_path):
    cwd = str(tmp_path)
    base = datetime.now(UTC)
    atomic_write_json(
        state_path(bridge_home),
        {
            "sessions": [
                _dead_session(
                    f"sess_{index:03d}",
                    cwd,
                    iso(base + timedelta(seconds=index)),
                ).model_dump(mode="json")
                for index in range(60)
            ],
            "tasks": [],
        },
    )
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        assert len(registry.sessions) == 50
        disk = read_json(state_path(bridge_home), {})
        assert len(disk["sessions"]) == 50
        assert {row["session_id"] for row in disk["sessions"]} == set(registry.sessions)
    finally:
        await registry.stop()


def test_get_transcript_survives_pruned_session(bridge_home):
    session_id = "sess_pruned"
    path = transcript_path(session_id, bridge_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"ts":"2026-01-01T00:00:00+00:00","type":"message_chunk","data":{"text":"kept"}}\n',
        encoding="utf-8",
    )
    registry = Registry.create(bridge_home)
    page = registry.get_transcript(session_id)
    assert page["total_matching"] == 1
    assert page["events"][0]["data"]["text"] == "kept"
    with pytest.raises(KeyError, match="unknown session"):
        registry.get_transcript("sess_never")


@pytest.mark.asyncio
async def test_stop_waits_for_running_tasks(bridge_home, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_FAKE_DELAY", "5")
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    dispatched = await registry.dispatch_task("fake", "slow", cwd=str(work.resolve()))
    await asyncio.sleep(0.1)
    assert registry.tasks[dispatched["task_id"]].status == TaskStatus.running
    started = time.monotonic()
    await registry.stop()
    assert time.monotonic() - started < 3
    assert registry.tasks[dispatched["task_id"]].status == TaskStatus.cancelled
    assert all(task.done() for task in registry._bg.values())
    assert registry._idle == {}
    payload = read_json(state_path(bridge_home), {})
    row = next(item for item in payload["tasks"] if item["task_id"] == dispatched["task_id"])
    assert row["status"] == "cancelled"


def test_schedule_idle_noop_while_stopping(bridge_home, tmp_path):
    registry = Registry.create(bridge_home)
    registry.sessions["sess_idle"] = Session(
        session_id="sess_idle",
        agent="grok",
        cwd=str(tmp_path),
    )
    registry._stopping = True
    registry._schedule_idle("sess_idle")
    assert registry._idle == {}


@pytest.mark.asyncio
async def test_stall_watchdog_fails_silent_turn(bridge_home, tmp_path, monkeypatch):
    monkeypatch.setattr("agent_bridge.registry.STALL_POLL_SEC", 0.2)
    monkeypatch.setenv("AGENT_BRIDGE_FAKE_DELAY", "10")
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    registry.config.agents["fake"].stall_timeout_sec = 1
    await registry.start()
    try:
        started = time.monotonic()
        dispatched = await registry.dispatch_task("fake", "silent", cwd=str(work.resolve()))
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=8)
        assert time.monotonic() - started < 6
        assert waited["status"] == "failed"
        assert waited["stop_reason"] == "stalled"
        assert waited["error"] is not None and "no output for 1s" in waited["error"]
        assert waited["silent_for_sec"] is None
        assert "stall_timeout_sec" in (waited.get("hint") or "")
        assert registry._bg == {}
        events = read_events(dispatched["session_id"], bridge_home)
        assert any(
            event.get("type") == "error" and (event.get("data") or {}).get("stalled") is True
            for event in events
        )
        await registry.flush_state()
        payload = read_json(state_path(bridge_home), {})
        row = next(item for item in payload["tasks"] if item["task_id"] == dispatched["task_id"])
        assert row["status"] == "failed"
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_stall_watchdog_resets_on_worker_output(bridge_home, tmp_path, monkeypatch):
    monkeypatch.setattr("agent_bridge.registry.STALL_POLL_SEC", 0.2)
    monkeypatch.setenv("AGENT_BRIDGE_FAKE_DELAY", "2.5")
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    registry.config.agents["fake"].stall_timeout_sec = 1
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("fake", "ticking", cwd=str(work.resolve()))
        task_id = dispatched["task_id"]
        session_id = dispatched["session_id"]

        async def _tick() -> None:
            while registry.tasks[task_id].status not in TERMINAL_STATUSES:
                append_event(session_id, "message_chunk", {"text": "tick"}, bridge_home)
                await asyncio.sleep(0.3)

        ticker = asyncio.create_task(_tick())
        try:
            waited = await registry.wait_task(task_id, timeout_sec=8)
            assert waited["status"] == "completed"
        finally:
            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_stall_watchdog_disabled_when_zero(bridge_home, tmp_path, monkeypatch):
    async def _boom(*args, **kwargs):
        raise AssertionError("_stall_watch should not start when stall_timeout_sec is 0")

    monkeypatch.setattr(Registry, "_stall_watch", _boom)
    monkeypatch.setenv("AGENT_BRIDGE_FAKE_DELAY", "0.3")
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    registry.config.agents["fake"].stall_timeout_sec = 0
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("fake", "ok", cwd=str(work.resolve()))
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert waited["status"] == "completed"
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_check_task_reports_silent_for_sec(bridge_home, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_FAKE_DELAY", "2")
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("fake", "slow", cwd=str(work.resolve()))
        await asyncio.sleep(0.5)
        checked = registry.check_task(dispatched["task_id"])
        assert isinstance(checked["silent_for_sec"], int)
        assert 0 <= checked["silent_for_sec"] <= 2
        assert checked["stall_timeout_sec"] == 1800
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        assert waited["status"] == "completed"
        assert waited["silent_for_sec"] is None
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_cancel_immediately_after_dispatch_returns_fast(bridge_home, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("fake", "tiny", cwd=str(work.resolve()))
        started = time.monotonic()
        cancelled = await registry.cancel_task(dispatched["task_id"])
        assert time.monotonic() - started < 3
        assert cancelled["status"] == "cancelled"
        assert registry._bg == {}
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=1)
        assert waited["status"] == "cancelled"
        assert registry.sessions[dispatched["session_id"]].proc_state != ProcState.spawning
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_cancel_task_still_wins_over_stall(bridge_home, tmp_path, monkeypatch):
    monkeypatch.setattr("agent_bridge.registry.STALL_POLL_SEC", 0.2)
    monkeypatch.setenv("AGENT_BRIDGE_FAKE_DELAY", "10")
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    registry.config.agents["fake"].stall_timeout_sec = 1
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("fake", "slow", cwd=str(work.resolve()))
        await asyncio.sleep(0.05)
        cancelled = await registry.cancel_task(dispatched["task_id"])
        assert cancelled["status"] == "cancelled"
        assert cancelled["stop_reason"] == "cancelled"
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_save_writes_off_the_event_loop(bridge_home, tmp_path, monkeypatch):
    writer_threads: list[int] = []

    def spy(path, payload):
        writer_threads.append(threading.get_ident())
        return atomic_write_json(path, payload)

    monkeypatch.setattr("agent_bridge.registry.atomic_write_json", spy)
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("fake", "tiny", cwd=str(work.resolve()))
        await registry.wait_task(dispatched["task_id"], timeout_sec=5)
        await registry.flush_state()
        main = threading.get_ident()
        assert writer_threads
        assert all(tid != main for tid in writer_threads)
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_save_coalesces_bursts(bridge_home, tmp_path, monkeypatch):
    writes: list[object] = []

    def spy(path, payload):
        writes.append(payload)
        return atomic_write_json(path, payload)

    monkeypatch.setattr("agent_bridge.registry.atomic_write_json", spy)
    registry = Registry.create(bridge_home)
    await registry.start()
    try:
        start_writes = len(writes)
        registry.sessions["sess_burst"] = Session(
            session_id="sess_burst",
            agent="fake",
            cwd=str(tmp_path),
        )
        for _ in range(20):
            registry.save()
        await registry.flush_state()
        assert len(writes) - start_writes <= 2
        payload = read_json(state_path(bridge_home), {})
        assert any(row["session_id"] == "sess_burst" for row in payload["sessions"])
    finally:
        await registry.stop()


def test_save_without_running_loop_writes_synchronously(bridge_home, tmp_path):
    registry = Registry.create(bridge_home)
    registry.sessions["sess_sync"] = Session(
        session_id="sess_sync",
        agent="fake",
        cwd=str(tmp_path),
    )
    registry.save()
    payload = read_json(state_path(bridge_home), {})
    assert any(row["session_id"] == "sess_sync" for row in payload["sessions"])


@pytest.mark.asyncio
async def test_stop_flushes_state(bridge_home, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    await registry.start()
    dispatched = await registry.dispatch_task("fake", "tiny", cwd=str(work.resolve()))
    await registry.wait_task(dispatched["task_id"], timeout_sec=5)
    await registry.stop()
    payload = read_json(state_path(bridge_home), {})
    row = next(item for item in payload["tasks"] if item["task_id"] == dispatched["task_id"])
    assert row["status"] == "completed"


@pytest.mark.asyncio
async def test_stall_cancel_finishes_after_turn_returns(bridge_home, tmp_path, monkeypatch):
    """The turn usually returns before adapter.cancel() has finished reaping;
    tearing down the watchdog must not abort that cleanup halfway."""
    monkeypatch.setattr("agent_bridge.registry.STALL_POLL_SEC", 0.2)
    monkeypatch.setenv("AGENT_BRIDGE_FAKE_DELAY", "10")
    cancel_finished = asyncio.Event()
    original_cancel = FakeAdapter.cancel

    async def slow_cancel(self, session):
        await original_cancel(self, session)
        await asyncio.sleep(0.5)
        cancel_finished.set()

    monkeypatch.setattr(FakeAdapter, "cancel", slow_cancel)
    work = tmp_path / "work"
    work.mkdir()
    registry = Registry.create(bridge_home)
    registry.config.agents["fake"].stall_timeout_sec = 1
    await registry.start()
    try:
        dispatched = await registry.dispatch_task("fake", "silent", cwd=str(work.resolve()))
        waited = await registry.wait_task(dispatched["task_id"], timeout_sec=8)
        assert waited["stop_reason"] == "stalled"
        assert not cancel_finished.is_set()
        await asyncio.sleep(0.8)
        assert cancel_finished.is_set()
    finally:
        await registry.stop()
