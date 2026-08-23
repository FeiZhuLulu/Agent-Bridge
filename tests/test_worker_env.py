from agent_bridge.config import EnvConfig
from agent_bridge.worker_env import (
    WORKER_CONTEXT_ENV,
    WORKER_CONTEXT_VALUE,
    apply_proxy_fallbacks,
    build_worker_env,
    describe_env,
    is_worker_context,
    parse_powershell_grok_proxy,
    parse_win_inet_proxy_server,
    redact_proxy_url,
    resolve_env,
)


GROK_PROFILE = """
function grok {
    $local:old_http = $env:HTTP_PROXY
    $local:old_https = $env:HTTPS_PROXY
    $env:HTTP_PROXY = "http://127.0.0.1:7897"
    $env:HTTPS_PROXY = "http://127.0.0.1:7897"
    try {
        & "$env:USERPROFILE\\.grok\\bin\\grok.exe" @args
    } finally {
        $env:HTTP_PROXY = $local:old_http
        $env:HTTPS_PROXY = $local:old_https
    }
}
"""


def test_parse_powershell_grok_proxy():
    parsed = parse_powershell_grok_proxy(GROK_PROFILE)
    assert parsed["HTTP_PROXY"] == "http://127.0.0.1:7897"
    assert parsed["HTTPS_PROXY"] == "http://127.0.0.1:7897"


OPENCODE_PROFILE = """
function opencode {
    $env:HTTP_PROXY = "http://127.0.0.1:7897"
    $env:HTTPS_PROXY = "http://127.0.0.1:7897"
    $env:ALL_PROXY = "http://127.0.0.1:7897"
    E:\\npm-global\\node_modules\\opencode-ai\\bin\\opencode.exe @args
}
"""


def test_parse_powershell_opencode_proxy():
    parsed = parse_powershell_grok_proxy(OPENCODE_PROFILE)
    assert parsed["HTTPS_PROXY"] == "http://127.0.0.1:7897"
    assert parsed["ALL_PROXY"] == "http://127.0.0.1:7897"


def test_parse_win_inet_proxy_server_simple():
    parsed = parse_win_inet_proxy_server("127.0.0.1:7897")
    assert parsed["HTTPS_PROXY"] == "http://127.0.0.1:7897"
    assert parsed["HTTP_PROXY"] == "http://127.0.0.1:7897"


def test_parse_win_inet_proxy_server_scheme_map():
    parsed = parse_win_inet_proxy_server("http=127.0.0.1:7890;https=127.0.0.1:7897")
    assert parsed["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert parsed["HTTPS_PROXY"] == "http://127.0.0.1:7897"


def test_apply_proxy_fallbacks_does_not_override_existing():
    env = apply_proxy_fallbacks(
        {"HTTPS_PROXY": "http://already:1"},
        {"HTTPS_PROXY": "http://discovered:2", "HTTP_PROXY": "http://discovered:2"},
    )
    assert env["HTTPS_PROXY"] == "http://already:1"
    assert env["HTTP_PROXY"] == "http://discovered:2"


def test_build_worker_env_fills_from_fallbacks_and_mirrors():
    env = build_worker_env(
        base={"PATH": "C:\\Windows"},
        fallbacks={"HTTP_PROXY": "http://127.0.0.1:7897"},
        fallback_origin={"HTTP_PROXY": "powershell-grok"},
        user_env={},
        machine_env={},
        log_fill=False,
    )
    assert env["HTTP_PROXY"] == "http://127.0.0.1:7897"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7897"
    assert env["ALL_PROXY"] == "http://127.0.0.1:7897"
    assert env["GROK_WEB_FETCH_PROXY"] == "http://127.0.0.1:7897"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_agent_env_overrides_discovered_proxy():
    env = build_worker_env(
        {"HTTPS_PROXY": "http://from-toml:9"},
        base={},
        fallbacks={"HTTPS_PROXY": "http://127.0.0.1:7897"},
        user_env={},
        machine_env={},
        log_fill=False,
    )
    assert env["HTTPS_PROXY"] == "http://from-toml:9"


def test_config_proxy_beats_process_and_discovery():
    env, origin = resolve_env(
        EnvConfig(
            discover_proxy=True,
            inherit=["HTTPS_PROXY", "DEEPSEEK_API_KEY"],
            proxy_url="http://pin:1",
        ),
        base={"HTTPS_PROXY": "http://process:2"},
        fallbacks={"HTTPS_PROXY": "http://discover:3"},
        user_env={"HTTPS_PROXY": "http://user:4", "DEEPSEEK_API_KEY": "sk-user"},
        machine_env={},
    )
    assert env["HTTPS_PROXY"] == "http://pin:1"
    assert origin["HTTPS_PROXY"] == "config.proxy"
    assert env["DEEPSEEK_API_KEY"] == "sk-user"
    assert origin["DEEPSEEK_API_KEY"] == "user-env"


def test_process_proxy_beats_inherit_when_unpinned():
    env, origin = resolve_env(
        EnvConfig(discover_proxy=False, inherit=["HTTPS_PROXY"]),
        base={"HTTPS_PROXY": "http://process:2"},
        user_env={"HTTPS_PROXY": "http://user:4"},
        machine_env={},
    )
    assert env["HTTPS_PROXY"] == "http://process:2"
    assert origin["HTTPS_PROXY"] == "process"


def test_describe_env_direct_network_has_empty_warnings():
    status = describe_env(
        EnvConfig(discover_proxy=False, inherit=[]),
        env={},
        origin={},
    )
    assert status["proxy"] is None
    assert status["warnings"] == []


def test_describe_env_omits_secret_values():
    status = describe_env(
        EnvConfig(discover_proxy=False, inherit=["HTTPS_PROXY", "DEEPSEEK_API_KEY"]),
        base={"HTTPS_PROXY": "http://user:pass@127.0.0.1:9", "DEEPSEEK_API_KEY": "sk-secret"},
        env=None,
        origin=None,
    )
    assert status["proxy"] == "http://***@127.0.0.1:9"
    assert "DEEPSEEK_API_KEY" in status["present"]
    assert "sk-secret" not in str(status)


def test_redact_proxy_credentials():
    assert redact_proxy_url("http://user:pass@127.0.0.1:7897") == "http://***@127.0.0.1:7897"


def test_worker_context_forced_after_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_HOME", str(tmp_path))
    env = build_worker_env(
        {WORKER_CONTEXT_ENV: "coordinator"},
        base={},
        user_env={},
        machine_env={},
        log_fill=False,
        worker_context=True,
    )
    assert env[WORKER_CONTEXT_ENV] == WORKER_CONTEXT_VALUE
    assert env["AGENT_BRIDGE_HOME"] == str((tmp_path / "nested").resolve())


def test_default_build_worker_env_does_not_add_mark():
    env = build_worker_env(
        base={WORKER_CONTEXT_ENV: "coordinator"},
        user_env={},
        machine_env={},
        log_fill=False,
    )
    assert env.get(WORKER_CONTEXT_ENV) != WORKER_CONTEXT_VALUE
    assert WORKER_CONTEXT_ENV not in build_worker_env(
        base={},
        user_env={},
        machine_env={},
        log_fill=False,
    )


def test_is_worker_context_strict_value():
    assert is_worker_context({WORKER_CONTEXT_ENV: WORKER_CONTEXT_VALUE}) is True
    assert is_worker_context({WORKER_CONTEXT_ENV: "coordinator"}) is False
    assert is_worker_context({}) is False
    assert is_worker_context({WORKER_CONTEXT_ENV: "Worker"}) is False
