from __future__ import annotations

import pytest

from agent_bridge.adapters.fake import FakeAdapter
from agent_bridge.baselines import Baseline
from agent_bridge.registry import Registry


@pytest.fixture
def dsh_home(bridge_home):
    bridge_home.mkdir(parents=True, exist_ok=True)
    (bridge_home / "agents.toml").write_text(
        '[agents.dsh]\nprotocol = "fake"\n', encoding="utf-8"
    )
    return bridge_home


@pytest.mark.asyncio
async def test_named_forks_keep_origin_and_labels_across_restart(dsh_home, tmp_path, monkeypatch):
    work = tmp_path / "project"
    work.mkdir()
    cwd = str(work.resolve())
    registry = Registry.create(dsh_home)
    async def connected(_home):
        return True
    monkeypatch.setattr("agent_bridge.registry.desktop_available", connected)
    monkeypatch.setattr(registry, "_adapter_for", lambda session: FakeAdapter(registry.config.get("dsh"), dsh_home))
    baseline = Baseline(
        baseline_id="base_v1",
        name="查清初始化路径",
        agent="dsh",
        backend="desktop",
        cwd=cwd,
        native_session_id="native-baseline-v1",
        source_session_id="sess_original",
    )
    registry.baselines.add(baseline)
    await registry.start()
    try:
        await registry.set_default_baseline(cwd, "dsh", baseline.baseline_id)
        first = await registry.dispatch_task(
            "dsh", "排查缓存", cwd, fork_label="缓存问题 A"
        )
        assert first["baseline_id"] == baseline.baseline_id
        assert first["fork_label"] == "缓存问题 A"
        result = await registry.wait_task(first["task_id"], timeout_sec=5)
        assert result["status"] == "completed"
        assert result["baseline_name"] == baseline.name
        assert result["fork_label"] == "缓存问题 A"

        second = await registry.dispatch_task(
            "dsh", "独立排查", cwd, baseline="base_v1", fork_label="独立分支 B"
        )
        await registry.wait_task(second["task_id"], timeout_sec=5)
        empty = await registry.dispatch_task("dsh", "从空白开始", cwd, baseline="empty")
        await registry.wait_task(empty["task_id"], timeout_sec=5)
        assert empty["baseline_id"] is None
        assert empty["fork_label"] is None
        with pytest.raises(ValueError, match="requires a selected baseline"):
            await registry.dispatch_task("dsh", "bad", cwd, baseline="empty", fork_label="bad")
        with pytest.raises(ValueError, match="only be supplied"):
            await registry.dispatch_task(
                "dsh", "bad", cwd, session_id=first["session_id"], fork_label="bad"
            )
    finally:
        await registry.stop()

    reopened = Registry.create(dsh_home)
    await reopened.start()
    try:
        rows = {row["session_id"]: row for row in reopened.list_sessions()}
        assert rows[first["session_id"]]["baseline_name"] == baseline.name
        assert rows[first["session_id"]]["fork_source_id"] == baseline.native_session_id
        assert rows[first["session_id"]]["fork_label"] == "缓存问题 A"
        assert rows[second["session_id"]]["fork_label"] == "独立分支 B"
        assert reopened.list_baselines(cwd)["default_baseline_id"] == baseline.baseline_id
        await reopened.set_default_baseline(cwd, "dsh", None)
        assert reopened.list_baselines(cwd)["default_baseline_id"] is None
    finally:
        await reopened.stop()


@pytest.mark.asyncio
async def test_create_baseline_saves_an_independent_fork_without_changing_source(
    dsh_home, tmp_path, monkeypatch
):
    work = tmp_path / "project"
    work.mkdir()
    registry = Registry.create(dsh_home)

    class ForkingFakeAdapter(FakeAdapter):
        def can_fork(self):
            return True

        async def ensure_session(self, session):
            if session.fork_source_id and session.native_session_id is None:
                session.native_session_id = f"fork-of-{session.fork_source_id}"
            await super().ensure_session(session)

    adapter = ForkingFakeAdapter(registry.config.get("dsh"), dsh_home)
    async def connected(_home):
        return True
    monkeypatch.setattr("agent_bridge.registry.desktop_available", connected)
    monkeypatch.setattr(registry, "_adapter_for", lambda session: adapter)
    await registry.start()
    try:
        source = await registry.dispatch_task("dsh", "调查公共信息", str(work.resolve()))
        assert (await registry.wait_task(source["task_id"], timeout_sec=5))["status"] == "completed"
        source_native = registry.sessions[source["session_id"]].native_session_id
        assert registry.sessions[source["session_id"]].backend == "desktop"
        saved = await registry.create_baseline(source["session_id"], "基线 1")
        record = registry.baselines.get(saved["baseline"]["baseline_id"])
        assert record.source_session_id == source["session_id"]
        assert record.native_session_id == f"fork-of-{source_native}"
        assert registry.sessions[source["session_id"]].native_session_id == source_native
        assert registry.sessions[saved["session_id"]].is_baseline is True

        await registry.set_default_baseline(str(work.resolve()), "dsh", record.baseline_id)
        child = await registry.dispatch_task(
            "dsh", "排查独立问题", str(work.resolve()), fork_label="分支 1"
        )
        assert child["baseline_id"] == record.baseline_id
        assert (await registry.wait_task(child["task_id"], timeout_sec=5))["status"] == "completed"
    finally:
        await registry.stop()
