from __future__ import annotations

import pytest


@pytest.fixture
def bridge_home(tmp_path, monkeypatch):
    home = tmp_path / "bridge-home"
    monkeypatch.setenv("AGENT_BRIDGE_HOME", str(home))
    monkeypatch.setenv("AGENT_BRIDGE_ENABLE_FAKE", "1")
    return home
