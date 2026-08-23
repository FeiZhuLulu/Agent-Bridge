from pathlib import Path

from agent_bridge.paths import WORKER_CONTEXT_ENV, WORKER_CONTEXT_VALUE, bridge_home, nested_bridge_home


def test_nested_bridge_home_appends_once(tmp_path: Path):
    first = nested_bridge_home(tmp_path)
    assert first == (tmp_path / "nested").resolve()
    assert nested_bridge_home(first) == first


def test_bridge_home_redirects_only_in_worker_context(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_HOME", str(tmp_path))
    monkeypatch.delenv(WORKER_CONTEXT_ENV, raising=False)
    assert bridge_home() == tmp_path.resolve()
    monkeypatch.setenv(WORKER_CONTEXT_ENV, WORKER_CONTEXT_VALUE)
    assert bridge_home() == (tmp_path / "nested").resolve()
    monkeypatch.setenv("AGENT_BRIDGE_HOME", str(tmp_path / "nested"))
    assert bridge_home() == (tmp_path / "nested").resolve()
