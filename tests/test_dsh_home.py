from pathlib import Path

from agent_bridge.dsh_home import (
    api_key_env_names,
    apply_dsh_worker_env,
    default_model,
    discovered_dsh_acp_commands,
    dsh_command_problem,
    dsh_cordis_for_launch,
    dsh_home,
    resolve_dsh_command,
    unwrap_npm_shim,
    with_bridge_cordis,
)
from agent_bridge.paths import bundled_dsh_cordis


def test_reads_any_user_default_model(tmp_path: Path):
    (tmp_path / "settings.yaml").write_text(
        """
ui-onboarding:
  welcomeNoticeVersion: 1
agent-default-model:
  provider: acme-gateway
  model: acme-large
llm-pi-ai:
  providers:
    acme-gateway:
      apiKeyEnv: ACME_GATEWAY_API_KEY
""",
        encoding="utf-8",
    )
    text = (tmp_path / "settings.yaml").read_text(encoding="utf-8")
    assert default_model(text) == ("acme-gateway", "acme-large")
    assert api_key_env_names(text) == ["ACME_GATEWAY_API_KEY"]


def test_official_deepseek_settings_still_parse():
    text = """
agent-default-model:
  provider: deepseek-official
  model: deepseek-v4-pro
llm-deepseek:
  apiKeyEnv: DEEPSEEK_API_KEY
"""
    assert default_model(text) == ("deepseek-official", "deepseek-v4-pro")
    assert "DEEPSEEK_API_KEY" in api_key_env_names(text)


def test_apply_env_uses_settings_not_a_hardcoded_vendor(tmp_path: Path):
    (tmp_path / "settings.yaml").write_text(
        """
agent-default-model:
  provider: opencode
  model: some-model
llm-pi-ai:
  providers:
    opencode:
      apiKeyEnv: OPENCODE_API_KEY
""",
        encoding="utf-8",
    )
    env = apply_dsh_worker_env(
        {"DSH_HOME": str(tmp_path)},
        user_env={"OPENCODE_API_KEY": "sk-from-user"},
        machine_env={},
    )
    assert env["DSH_ACP_PROVIDER"] == "opencode"
    assert env["DSH_ACP_MODEL"] == "some-model"
    assert env["OPENCODE_API_KEY"] == "sk-from-user"
    assert "DEEPSEEK_API_KEY" not in env
    assert "DSH_SNAPSHOT_SESSIONS_ROOT" not in env


def test_session_persistence_lives_under_bridge_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "bridge"
    monkeypatch.setenv("AGENT_BRIDGE_HOME", str(home))
    env = apply_dsh_worker_env({"DSH_HOME": str(tmp_path)}, session_id="sess_abc", user_env={}, machine_env={})
    root = Path(env["DSH_SNAPSHOT_SESSIONS_ROOT"])
    assert root == (home / "dsh-sessions" / "sess_abc").resolve()
    assert root.is_dir()
    assert root != tmp_path / ".sessions"


def test_coordinator_model_and_effort_override_settings(tmp_path: Path):
    (tmp_path / "settings.yaml").write_text(
        """
agent-default-model:
  provider: opencode
  model: some-model
""",
        encoding="utf-8",
    )
    env = apply_dsh_worker_env(
        {"DSH_HOME": str(tmp_path)},
        model="deepseek-official/deepseek-v4-flash",
        effort="low",
        user_env={},
        machine_env={},
    )
    assert env["DSH_ACP_PROVIDER"] == "deepseek-official"
    assert env["DSH_ACP_MODEL"] == "deepseek-v4-flash"
    assert env["DSH_ACP_REASONING_EFFORT"] == "low"


def test_user_sessions_root_wins(tmp_path: Path, monkeypatch):
    home = tmp_path / "bridge"
    custom = tmp_path / "mine-sessions"
    monkeypatch.setenv("AGENT_BRIDGE_HOME", str(home))
    env = apply_dsh_worker_env(
        {"DSH_HOME": str(tmp_path), "DSH_SNAPSHOT_SESSIONS_ROOT": str(custom)},
        session_id="sess_abc",
        user_env={},
        machine_env={},
    )
    assert env["DSH_SNAPSHOT_SESSIONS_ROOT"] == str(custom)


def test_existing_provider_overlay_wins(tmp_path: Path):
    (tmp_path / "settings.yaml").write_text(
        """
agent-default-model:
  provider: opencode
  model: some-model
""",
        encoding="utf-8",
    )
    env = apply_dsh_worker_env(
        {
            "DSH_HOME": str(tmp_path),
            "DSH_ACP_PROVIDER": "anthropic",
            "DSH_ACP_MODEL": "claude-sonnet-4-5",
        },
        user_env={},
        machine_env={},
    )
    assert env["DSH_ACP_PROVIDER"] == "anthropic"
    assert env["DSH_ACP_MODEL"] == "claude-sonnet-4-5"


def test_dsh_home_from_env(tmp_path: Path):
    assert dsh_home({"DSH_HOME": str(tmp_path)}) == tmp_path


def test_replaces_official_example_cordis(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("agent_bridge.dsh_home.dsh_acp_install_dir", lambda home=None: tmp_path / "missing")
    cordis = str(bundled_dsh_cordis())
    assert bundled_dsh_cordis().is_file()
    rewritten = with_bridge_cordis(
        [
            "node",
            "bin.ts",
            "--config",
            "/tmp/deepseek-harness/examples/acp-agent/cordis.yml",
        ]
    )
    assert rewritten[-1] == cordis
    assert "examples/acp-agent/cordis.yml" not in rewritten[-1]


def test_appends_bridge_cordis_when_missing(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("agent_bridge.dsh_home.dsh_acp_install_dir", lambda home=None: tmp_path / "missing")
    rewritten = with_bridge_cordis(["dsh-acp-demo"])
    assert rewritten[:3] == ["dsh-acp-demo", "--config", str(bundled_dsh_cordis())]


def test_keeps_explicit_custom_cordis(tmp_path: Path):
    custom = str(tmp_path / "mine.yml")
    rewritten = with_bridge_cordis(["dsh-acp-demo", "--config", custom])
    assert rewritten == ["dsh-acp-demo", "--config", custom]


def test_rejects_tsx_source_when_tsx_is_missing(monkeypatch, tmp_path: Path):
    source = tmp_path / "bin.ts"
    source.write_text("// demo\n", encoding="utf-8")
    monkeypatch.setattr("agent_bridge.dsh_home.find_tsx", lambda: None)
    problem = dsh_command_problem(["node", "--import", "tsx", str(source)])
    assert problem and "tsx" in problem


def test_rejects_missing_built_bin(tmp_path: Path):
    missing = tmp_path / "lib" / "bin.js"
    problem = dsh_command_problem(["node", str(missing)])
    assert problem and "bin missing" in problem


def test_skips_broken_tsx_fallback_to_local_install(monkeypatch, tmp_path: Path):
    js = tmp_path / "bin.js"
    js.write_text("console.log('ok')\n", encoding="utf-8")
    monkeypatch.setattr("agent_bridge.dsh_home.discovered_dsh_acp_commands", lambda: [])
    monkeypatch.setattr("agent_bridge.dsh_home.find_tsx", lambda: None)
    resolved = resolve_dsh_command(
        ["dsh-acp-demo-missing"],
        [
            ["node", "--import", "tsx", str(tmp_path / "src" / "bin.ts")],
            ["node", str(js)],
        ],
    )
    assert resolved[-1] == str(js)


def test_unwraps_windows_npm_shim(tmp_path: Path, monkeypatch):
    js = tmp_path / "node_modules" / "@deepseek-ai" / "dsh-acp-demo" / "lib" / "bin.js"
    js.parent.mkdir(parents=True)
    js.write_text("console.log(1)\n", encoding="utf-8")
    shim = tmp_path / "dsh-acp-demo.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr("agent_bridge.dsh_home.shutil.which", lambda name: "C:/node.exe" if name == "node" else None)
    unwrapped = unwrap_npm_shim([str(shim)])
    assert unwrapped is not None
    assert unwrapped[-1] == str(js)


def test_materializes_cordis_beside_acp_modules(tmp_path: Path):
    js = tmp_path / "node_modules" / "@deepseek-ai" / "dsh-acp-demo" / "lib" / "bin.js"
    js.parent.mkdir(parents=True)
    js.write_text("console.log(1)\n", encoding="utf-8")
    dest = dsh_cordis_for_launch(["node", str(js)])
    assert dest == tmp_path / "dsh-acp.cordis.yml"
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == bundled_dsh_cordis().read_text(encoding="utf-8")


def test_discovers_explicit_bin_and_harness_checkout(tmp_path: Path, monkeypatch):
    explicit = tmp_path / "explicit.js"
    explicit.write_text("console.log(1)\n", encoding="utf-8")
    built = tmp_path / "checkout" / "packages" / "examples" / "acp-demo" / "lib" / "bin.js"
    built.parent.mkdir(parents=True)
    built.write_text("console.log(1)\n", encoding="utf-8")
    monkeypatch.setenv("DSH_ACP_BIN", str(explicit))
    monkeypatch.setenv("DSH_HARNESS", str(tmp_path / "checkout"))
    monkeypatch.delenv("DEEPSEEK_HARNESS", raising=False)
    monkeypatch.delenv("DSH_CHECKOUT", raising=False)
    monkeypatch.setattr("agent_bridge.dsh_home.dsh_acp_install_dir", lambda home=None: tmp_path / "absent")
    monkeypatch.setattr("agent_bridge.dsh_home.npm_global_prefixes", lambda: [])
    monkeypatch.setattr("agent_bridge.dsh_home.shutil.which", lambda name: "C:/node.exe" if name == "node" else None)
    found = discovered_dsh_acp_commands()
    assert [explicit] == [Path(cmd[-1]) for cmd in found[:1]]
    assert any(Path(cmd[-1]) == built for cmd in found)


def test_unbuilt_checkout_without_tsx_is_ignored(tmp_path: Path, monkeypatch):
    source = tmp_path / "checkout" / "packages" / "examples" / "acp-demo" / "src" / "bin.ts"
    source.parent.mkdir(parents=True)
    source.write_text("// demo\n", encoding="utf-8")
    monkeypatch.delenv("DSH_ACP_BIN", raising=False)
    monkeypatch.setenv("DSH_HARNESS", str(tmp_path / "checkout"))
    monkeypatch.setattr("agent_bridge.dsh_home.find_tsx", lambda: None)
    monkeypatch.setattr("agent_bridge.dsh_home.dsh_acp_install_dir", lambda home=None: tmp_path / "absent")
    monkeypatch.setattr("agent_bridge.dsh_home.npm_global_prefixes", lambda: [])
    monkeypatch.setattr("agent_bridge.dsh_home.shutil.which", lambda name: "C:/node.exe" if name == "node" else None)
    assert discovered_dsh_acp_commands() == []
