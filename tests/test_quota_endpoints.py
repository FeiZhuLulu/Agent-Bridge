from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from agent_bridge.config import AgentConfig
from agent_bridge.quota import QuotaCache, QuotaProvider, QuotaStatus, fetch_quota
from agent_bridge.quota_claude import fetch_claude_quota
from agent_bridge.quota_codex import fetch_codex_quota
from agent_bridge.quota_endpoints import GROK_AUTH_SCOPE, official_url, quota_block_reason
from agent_bridge.quota_grok import fetch_grok_quota, grok_auth_headers
from agent_bridge.quota_kimi import fetch_kimi_quota, kimi_access_token

FETCH = {"codex": fetch_codex_quota, "kimi": fetch_kimi_quota, "grok": fetch_grok_quota, "claude": fetch_claude_quota}
CUSTOM_ENV = [
    ("codex", "OPENAI_BASE_URL"),
    ("kimi", "KIMI_CODE_BASE_URL"), ("kimi", "KIMI_BASE_URL"), ("kimi", "KIMI_CODE_OAUTH_HOST"),
    ("kimi", "KIMI_OAUTH_HOST"), ("kimi", "KIMI_MODEL_BASE_URL"),
    ("grok", "GROK_CLI_CHAT_PROXY_BASE_URL"), ("grok", "GROK_XAI_API_BASE_URL"),
    ("grok", "GROK_MODELS_BASE_URL"), ("grok", "GROK_MODELS_LIST_URL"),
    ("grok", "GROK_OIDC_ISSUER"), ("grok", "GROK_OAUTH2_ISSUER"),
    ("claude", "ANTHROPIC_BASE_URL"),
]


@pytest.fixture(autouse=True)
def isolated_cli_config(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    for key in ("GROK_HOME", "KIMI_CODE_HOME"):
        monkeypatch.delenv(key, raising=False)


def cfg(name):
    return AgentConfig(name=name, protocol="codex" if name == "codex" else "acp", command=[name])


@pytest.mark.parametrize(("name", "key"), CUSTOM_ENV)
async def test_custom_endpoint_rejects_cache_and_direct_provider_before_credentials(name, key, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unsupported endpoints must not read credentials, launch a CLI, or issue HTTP")

    for target in (
        "agent_bridge.quota_kimi.kimi_access_token", "agent_bridge.quota_grok.grok_auth_headers",
        "agent_bridge.quota_grok._grok_version", "agent_bridge.quota_claude.describe_claude_auth",
        "agent_bridge.quota_codex.resolve_codex_command", "agent_bridge.quota_codex.read_codex_rate_limits",
    ):
        monkeypatch.setattr(target, forbidden)
    cache = QuotaCache(300)
    cache.put(name, QuotaStatus(status="ok", plan="old official account"))
    env = {key: "https://custom.invalid/private?secret=do-not-show"}
    row = await fetch_quota(cfg(name), env, cache=cache, timeout_sec=1, providers={name: QuotaProvider("test", forbidden)})
    assert row["status"] == "unknown"
    assert "custom endpoints" in row["detail"]
    assert "do-not-show" not in row["detail"]
    assert not row["cached"]
    assert cache.get(name) is None and cache.last_good(name) is None
    assert (await FETCH[name](cfg(name), env)).status == "unknown"
    await cache.close()


async def test_custom_endpoint_detaches_inflight_without_restoring_old_account():
    cache = QuotaCache(300)
    started, release = asyncio.Event(), asyncio.Event()

    async def old(cfg, env):
        started.set()
        await release.wait()
        return QuotaStatus(status="ok", plan="old account")

    first = asyncio.create_task(fetch_quota(cfg("kimi"), {}, cache=cache, timeout_sec=1,
                                         providers={"kimi": QuotaProvider(None, old)}))
    await started.wait()
    blocked = await fetch_quota(cfg("kimi"), {"KIMI_CODE_BASE_URL": "https://custom.invalid"}, cache=cache, timeout_sec=1)
    assert blocked["status"] == "unknown"
    release.set()
    await first
    assert cache.get("kimi") is None and cache.last_good("kimi") is None
    await cache.close()


@pytest.mark.parametrize(("name", "flags"), [
    ("codex", ["--oss"]), ("codex", ["--local-provider=ollama"]),
    ("codex", ["-c", 'model_provider="custom"']),
    ("codex", ['-cmodel_provider="custom"']),
    ("kimi", ["--config-file", "custom.toml"]),
    ("grok", ["--cli-chat-proxy-base-url", "https://custom.invalid"]),
    ("claude", ["--settings", "custom.json"]),
])
async def test_fallback_endpoint_overrides_cannot_reuse_official_cache(name, flags):
    worker = cfg(name).model_copy(update={"fallback_commands": [[name, *flags]]})
    cache = QuotaCache(300)
    cache.put(name, QuotaStatus(status="ok", plan="official"))
    try:
        row = await fetch_quota(worker, {}, cache=cache, timeout_sec=1)
        assert row["status"] == "unknown" and not row["cached"]
        assert cache.get(name) is None and cache.last_good(name) is None
    finally:
        await cache.close()


def test_codex_agent_config_overrides_block_quota_lookup():
    # config_overrides become `codex exec -c` pairs; like `-c` flags in
    # command they can select a custom provider, so fail closed.
    agent = AgentConfig(
        name="codex",
        protocol="codex",
        command=["codex"],
        config_overrides=['model_provider="my-gateway"'],
    )
    reason = quota_block_reason(agent, {})
    assert reason is not None and "configuration overrides" in reason


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize(("name", "folder", "flag", "text"), [
    ("codex", ".codex", "-pwork", '[profiles.work]\nmodel_provider="custom"'),
    ("kimi", ".kimi-code", "-mcustom", 'default_model="kimi-code/k3"\n[models.custom]\nprovider="custom"\n[providers.custom]\ntype="kimi"\nbase_url="https://custom.invalid/v1"'),
    ("grok", ".grok", "-mcustom", '[models]\ndefault="grok-4.6"\n[model.custom]\nbase_url="https://custom.invalid/v1"'),
])
async def test_attached_short_selector_blocks_custom_primary_and_fallback(tmp_path, name, folder, flag, text, fallback):
    home = tmp_path / folder
    home.mkdir()
    (home / "config.toml").write_text(text, encoding="utf-8")
    worker = cfg(name)
    if fallback:
        worker.fallback_commands = [[name, flag]]
    else:
        worker.command.append(flag)
    cache = QuotaCache(300)
    cache.put(name, QuotaStatus(status="ok", plan="official"))
    try:
        row = await fetch_quota(worker, {}, cache=cache, timeout_sec=1)
        assert row["status"] == "unknown" and not row["cached"]
        assert cache.last_good(name) is None
    finally:
        await cache.close()


@pytest.mark.parametrize(("name", "folder", "text"), [
    ("codex", ".codex", 'model_provider = "gateway"'),
    ("codex", ".codex", '[model_providers.openai]\nbase_url = "https://custom.invalid/v1"'),
    ("codex", ".codex", 'profile = "work"\n[profiles.work]\nmodel_provider = "gateway"'),
    ("kimi", ".kimi-code", 'default_model="custom"\n[models.custom]\nprovider="p"\n[providers.p]\ntype="kimi"\nbase_url="https://custom.invalid/v1"'),
    ("kimi", ".kimi-code", 'default_model="custom"\n[models.custom]\nprovider="p"\nbase_url="https://custom.invalid/v1"\n[providers.p]\ntype="kimi"\nbase_url="https://api.kimi.com/coding/v1"'),
    ("kimi", ".kimi-code", 'default_model="custom"\n[models.custom]\nprovider="p"\n[providers.p]\ntype="kimi"\n[providers.p.env]\nKIMI_BASE_URL="https://custom.invalid/v1"'),
    ("grok", ".grok", '[endpoints]\ncli_chat_proxy_base_url="https://custom.invalid/v1"'),
    ("grok", ".grok", '[models]\ndefault="mine"\n[model.mine]\nbase_url="https://custom.invalid/v1"'),
    ("grok", ".grok", '[grok_com_config.oidc]\nissuer="https://enterprise.invalid"\nclient_id="corp"'),
])
def test_custom_endpoint_in_selected_cli_configuration(tmp_path, name, folder, text):
    home = tmp_path / folder
    home.mkdir()
    (home / "config.toml").write_text(text, encoding="utf-8")
    assert quota_block_reason(cfg(name), {}) is not None


def test_claude_settings_endpoint_is_rejected(tmp_path):
    home = tmp_path / ".claude"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://custom.invalid"}}))
    assert "custom endpoints" in quota_block_reason(cfg("claude"), {})


@pytest.mark.parametrize("name,folder", [("codex", ".codex"), ("kimi", ".kimi-code"), ("grok", ".grok")])
def test_unreadable_configuration_is_unknown(tmp_path, name, folder):
    home = tmp_path / folder
    home.mkdir()
    (home / "config.toml").write_text("this is not TOML")
    assert "cannot verify" in quota_block_reason(cfg(name), {})


def test_official_defaults_and_network_proxies_remain_supported():
    for name in FETCH:
        assert quota_block_reason(cfg(name), {"HTTPS_PROXY": "http://proxy.invalid:7897", "NO_PROXY": "localhost"}) is None
    assert quota_block_reason(cfg("kimi"), {"KIMI_CODE_BASE_URL": "https://api.kimi.com:443/coding/v1/"}) is None
    assert quota_block_reason(cfg("kimi"), {"OPENAI_BASE_URL": "https://another-cli.invalid"}) is None
    assert quota_block_reason(AgentConfig(name="renamed", protocol="codex", command=["codex"]),
                              {"OPENAI_BASE_URL": "https://custom.invalid"}) is not None
    assert not official_url("https://auth.x.ai.attacker.invalid", "https://auth.x.ai")
    assert not official_url("https://auth.x.ai/other", "https://auth.x.ai")


def test_kimi_never_guesses_another_credential_file(tmp_path):
    (tmp_path / "credentials").mkdir()
    (tmp_path / "credentials" / "other.json").write_text('{"access_token":"wrong-account"}')
    token, reason = kimi_access_token(tmp_path)
    assert token is None and "no credential file" in reason


@pytest.mark.parametrize("credential", ['api_key="distributed-key"', '[providers.p.env]\nKIMI_API_KEY="distributed-key"'])
async def test_kimi_managed_api_key_provider_cannot_use_oauth_quota(tmp_path, monkeypatch, credential):
    home = tmp_path / ".kimi-code"
    home.mkdir()
    (home / "config.toml").write_text(
        'default_model="custom"\n[models.custom]\nprovider="p"\n[providers.p]\n'
        'type="kimi"\nbase_url="https://api.kimi.com/coding/v1"\n' + credential,
        encoding="utf-8",
    )

    def forbidden(*args, **kwargs):
        pytest.fail("API-key providers must not read OAuth credentials or issue quota HTTP")

    monkeypatch.setattr("agent_bridge.quota_kimi.kimi_access_token", forbidden)
    monkeypatch.setattr("agent_bridge.quota_kimi.get_json", forbidden)
    reason = quota_block_reason(cfg("kimi"), {})
    assert reason is not None and "API-key" in reason
    status = await fetch_kimi_quota(cfg("kimi"), {})
    assert status.status == "unknown" and "API-key" in (status.detail or "")


def test_kimi_default_oauth_provider_remains_supported(tmp_path):
    home = tmp_path / ".kimi-code"
    home.mkdir()
    (home / "config.toml").write_text(
        'default_model="kimi-code/k3"\n[models."kimi-code/k3"]\nprovider="managed:kimi-code"\n'
        '[providers."managed:kimi-code"]\ntype="kimi"\nbase_url="https://api.kimi.com/coding/v1"\n'
        'api_key=""\n[providers."managed:kimi-code".oauth]\nstorage="file"\nkey="kimi-code"',
        encoding="utf-8",
    )
    assert quota_block_reason(cfg("kimi"), {}) is None


@pytest.mark.parametrize("change", [
    {"oidc_issuer": "https://enterprise.invalid"}, {"oidc_issuer": "https://auth.x.ai.attacker.invalid"},
    {"auth_mode": "api_key"}, {"user_id": ""}, {"expires_at": None}, {"expires_at": "expired"},
])
def test_grok_never_uses_unverified_auth_even_in_default_scope(tmp_path, change):
    entry = {"key": "test", "auth_mode": "oidc", "user_id": "user", "oidc_issuer": "https://auth.x.ai",
             "expires_at": "2100-01-01T00:00:00Z", **change}
    (tmp_path / "auth.json").write_text(json.dumps({GROK_AUTH_SCOPE: entry}))
    headers, reason = grok_auth_headers(tmp_path, version="1.0.13")
    assert headers is None and reason


def test_grok_never_falls_back_from_missing_default_scope(tmp_path):
    (tmp_path / "auth.json").write_text(json.dumps({
        "https://auth.x.ai.fake::client": {"key": "wrong"},
        "https://enterprise.invalid::client": {"key": "enterprise"},
    }))
    headers, reason = grok_auth_headers(tmp_path, version="1.0.13")
    assert headers is None and "default Grok OAuth scope" in reason


async def test_grok_provider_sends_native_headers(tmp_path, monkeypatch):
    home = tmp_path / ".grok"
    home.mkdir()
    (home / "auth.json").write_text(json.dumps({GROK_AUTH_SCOPE: {
        "key": "official-test", "auth_mode": "oidc", "oidc_issuer": "https://auth.x.ai",
        "user_id": "test-user", "expires_at": "2100-01-01T00:00:00Z",
    }}))

    async def version(cfg, env):
        return "1.0.13"

    async def request(url, *, headers, **kwargs):
        assert url == "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
        assert headers == {"Authorization": "Bearer official-test", "X-XAI-Token-Auth": "xai-grok-cli",
                           "x-userid": "test-user", "x-grok-client-version": "1.0.13", "x-grok-client-mode": "headless"}
        return {"config": {"creditUsagePercent": 17}}

    monkeypatch.setattr("agent_bridge.quota_grok._grok_version", version)
    monkeypatch.setattr("agent_bridge.quota_grok.get_json", request)
    status = await fetch_grok_quota(cfg("grok"), {})
    assert status.status == "ok" and status.windows[0].remaining_percent == 83


async def test_cancelling_grok_version_reaps_child(monkeypatch):
    from agent_bridge.quota_grok import _grok_version

    created = asyncio.Event()
    processes = []
    real_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        process = await real_spawn(sys.executable, "-c", "import time; time.sleep(30)", **kwargs)
        processes.append(process)
        created.set()
        return process

    monkeypatch.setattr("agent_bridge.quota_grok.resolve_command", lambda *args: [sys.executable])
    monkeypatch.setattr("agent_bridge.quota_grok.asyncio.create_subprocess_exec", spawn)
    lookup = asyncio.create_task(_grok_version(cfg("grok"), {}))
    await created.wait()
    lookup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await lookup
    assert processes[0].returncode is not None
