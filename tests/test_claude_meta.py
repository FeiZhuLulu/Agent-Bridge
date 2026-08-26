from agent_bridge.claude_meta import (
    CLAUDE_MODE_BYPASS,
    OPENROUTER_ANTHROPIC_BASE,
    apply_claude_gateway_env,
    claude_config_home,
    describe_claude_auth,
    resolve_claude_effort,
)


def test_bypass_mode_id():
    assert CLAUDE_MODE_BYPASS == "bypassPermissions"


def test_effort_maps_onto_common_claude_variants():
    offered = ["default", "low", "medium", "high", "xhigh"]
    assert resolve_claude_effort("off", offered) == "default"
    assert resolve_claude_effort("low", offered) == "low"
    assert resolve_claude_effort("medium", offered) == "medium"
    assert resolve_claude_effort("high", offered) == "high"
    assert resolve_claude_effort("max", offered) == "xhigh"


def test_effort_prefers_an_exact_match():
    offered = ["off", "low", "medium", "high", "max"]
    for effort in ("off", "low", "medium", "high", "max"):
        assert resolve_claude_effort(effort, offered) == effort


def test_effort_max_prefers_ultracode_when_advertised():
    assert resolve_claude_effort("max", ["high", "ultracode"]) == "ultracode"


def test_no_variants_returns_none():
    assert resolve_claude_effort("high", []) is None
    assert resolve_claude_effort("high", ["turbo"]) is None
    assert resolve_claude_effort(None, ["high"]) is None


def test_gateway_env_maps_openrouter_when_there_is_no_anthropic_key():
    env = apply_claude_gateway_env({"OPENROUTER_API_KEY": "sk-or-x"})
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-or-x"
    assert env["ANTHROPIC_BASE_URL"] == OPENROUTER_ANTHROPIC_BASE
    assert env.get("ANTHROPIC_API_KEY") in (None, "")


def test_gateway_env_keeps_direct_anthropic_key_when_openrouter_is_also_set():
    env = apply_claude_gateway_env(
        {"OPENROUTER_API_KEY": "sk-or-x", "ANTHROPIC_API_KEY": "sk-ant-x"}
    )
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-x"
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_BASE_URL" not in env


def test_gateway_env_maps_openrouter_when_user_already_set_a_base_url():
    env = apply_claude_gateway_env(
        {
            "OPENROUTER_API_KEY": "sk-or-x",
            "ANTHROPIC_API_KEY": "sk-ant-x",
            "ANTHROPIC_BASE_URL": "https://example.invalid/api",
        }
    )
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-or-x"
    assert env["ANTHROPIC_BASE_URL"] == "https://example.invalid/api"
    assert env["ANTHROPIC_API_KEY"] == ""


def test_gateway_env_does_not_override_an_explicit_base_url():
    env = apply_claude_gateway_env(
        {
            "OPENROUTER_API_KEY": "sk-or-x",
            "ANTHROPIC_BASE_URL": "https://example.invalid/api",
        }
    )
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-or-x"
    assert env["ANTHROPIC_BASE_URL"] == "https://example.invalid/api"
    assert env["ANTHROPIC_API_KEY"] == ""


def test_gateway_env_leaves_direct_anthropic_key_alone():
    env = apply_claude_gateway_env({"ANTHROPIC_API_KEY": "sk-ant-x"})
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-x"
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_auth_reports_gateway_without_hiding_availability(tmp_path):
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path), "ANTHROPIC_AUTH_TOKEN": "sk-or-x", "ANTHROPIC_BASE_URL": OPENROUTER_ANTHROPIC_BASE}
    assert describe_claude_auth(env) == "gateway"
    assert claude_config_home(env) == tmp_path


def test_auth_reports_missing_without_a_login(tmp_path):
    assert "missing" in describe_claude_auth({"CLAUDE_CONFIG_DIR": str(tmp_path)})


def test_auth_does_not_treat_mcp_user_config_as_oauth(tmp_path):
    (tmp_path / ".claude.json").write_text(
        '{"mcpServers": {"agent-bridge": {"command": "agent-bridge"}}}',
        encoding="utf-8",
    )
    assert "missing" in describe_claude_auth({"CLAUDE_CONFIG_DIR": str(tmp_path)})


def test_auth_reports_oauth_from_credentials_file(tmp_path):
    (tmp_path / ".credentials.json").write_text("{}", encoding="utf-8")
    assert describe_claude_auth({"CLAUDE_CONFIG_DIR": str(tmp_path)}) == "oauth"


def test_auth_reports_oauth_only_when_user_config_has_an_account(tmp_path):
    (tmp_path / ".claude.json").write_text(
        '{"oauthAccount": {"accountUuid": "acct"}, "mcpServers": {}}',
        encoding="utf-8",
    )
    assert describe_claude_auth({"CLAUDE_CONFIG_DIR": str(tmp_path)}) == "oauth"
