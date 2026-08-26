from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent_bridge.adapters.fake import FakeAdapter
from agent_bridge.models import ProcState, Session, Task, TaskStatus, TurnResult, iso
from agent_bridge.paths import state_path
from agent_bridge.persist import atomic_write_json, read_json
from agent_bridge.registry import (
    NESTED_CANCEL_ERROR,
    NESTED_DISPATCH_ERROR,
    NESTED_END_SESSION_ERROR,
    NESTED_PREFERENCES_ERROR,
    Registry,
)


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
            payload = read_json(state_path(bridge_home), {})
            assert any(row["session_id"] == sess_a for row in payload["sessions"])
            assert any(row["task_id"] == task_a for row in payload["tasks"])

            other = await b.dispatch_task("fake", "other", cwd=cwd)
            payload = read_json(state_path(bridge_home), {})
            session_ids = {row["session_id"] for row in payload["sessions"]}
            assert {sess_a, other["session_id"]} <= session_ids
            assert any(row["task_id"] == task_a for row in payload["tasks"])

            a.save()
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

    async def write_then_ok(self, session, task):
        (Path(task.cwd) / "smoke.txt").write_text("hello-bridge\n", encoding="utf-8")
        hidden = Path(task.cwd) / ".sessions"
        hidden.mkdir()
        (hidden / "log.jsonl").write_text("{}\n", encoding="utf-8")
        return TurnResult(text="done", files_changed=[])

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
