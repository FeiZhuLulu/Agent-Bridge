from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from agent_bridge.paths import bundled_agents_toml

ROOT = Path(__file__).resolve().parents[1]


def test_bundled_agents_toml_matches_root_template():
    bundled = bundled_agents_toml().read_text(encoding="utf-8")
    root = (ROOT / "agents.toml").read_text(encoding="utf-8")
    assert bundled == root
    assert "[coordinator]" in bundled
    assert 'mode = "auto"' in bundled
    assert "instructions" in bundled


def test_wheel_ships_coordinator_defaults(tmp_path: Path):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to build the wheel")
    completed = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    wheels = sorted(tmp_path.glob("agent_bridge-*.whl"))
    assert wheels
    with zipfile.ZipFile(wheels[-1]) as archive:
        names = [name for name in archive.namelist() if name.endswith("agent_bridge/agents.toml")]
        assert names
        text = archive.read(names[0]).decode("utf-8")
    assert "[coordinator]" in text
    assert 'mode = "auto"' in text
    assert "instructions" in text
