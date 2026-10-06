from __future__ import annotations

from types import SimpleNamespace

from acp.schema import PermissionOption

from agent_bridge.adapters.acp import _pick_permission_option
from agent_bridge.diagnostics import redact_diagnostic


def _opt(kind: str, name: str = "") -> PermissionOption:
    return PermissionOption(optionId=f"{kind}-id", name=name or kind, kind=kind)  # type: ignore[arg-type]


def test_redact_diagnostic_scrubs_key_value_secrets():
    text = "auth failed\napi_key=tp-abcdef0123456789\nAuthorization: Bearer tp-abcdef0123456789"
    out = redact_diagnostic(text)
    assert "tp-abcdef0123456789" not in out
    assert "[redacted]" in out


def test_redact_diagnostic_scrubs_bare_tokens_and_urls():
    text = "leak sk-proj-abc123ghp_xyz789 then https://api.example.com/v1?k=secret"
    out = redact_diagnostic(text)
    assert "sk-proj-abc123" not in out
    assert "ghp_xyz789" not in out
    assert "api.example.com" not in out


def test_redact_diagnostic_strips_ansi_and_caps():
    assert "\x1b[31m" not in redact_diagnostic("\x1b[31mred\x1b[0m")
    assert len(redact_diagnostic("x" * 100_000)) <= 4096


def test_redact_diagnostic_keeps_plain_errors():
    assert redact_diagnostic("codex exited with code 1") == "codex exited with code 1"


def test_permission_default_policy_prefers_allow_once():
    options = [_opt("reject_once"), _opt("allow_always"), _opt("allow_once")]
    picked = _pick_permission_option(options)
    assert picked is not None and picked.kind == "allow_once"


def test_permission_allow_always_policy_picks_allow_always():
    options = [_opt("allow_once"), _opt("reject_once"), _opt("allow_always")]
    picked = _pick_permission_option(options, "allow_always")
    assert picked is not None and picked.kind == "allow_always"


def test_permission_allow_always_falls_back_to_allow_once():
    options = [_opt("reject_once"), _opt("allow_once")]
    picked = _pick_permission_option(options, "allow_always")
    assert picked is not None and picked.kind == "allow_once"


def test_permission_deny_policy_returns_none():
    options = [_opt("allow_once"), _opt("allow_always")]
    assert _pick_permission_option(options, "deny") is None


def test_permission_allow_once_falls_back_to_allow_always():
    options = [_opt("reject_once"), _opt("allow_always")]
    picked = _pick_permission_option(options, "allow_once")
    assert picked is not None and picked.kind == "allow_always"


def test_permission_unknown_policy_behaves_like_allow_once():
    options = [
        SimpleNamespace(kind="allow_always", option_id="always"),
        SimpleNamespace(kind="allow_once", option_id="once"),
    ]
    picked = _pick_permission_option(options, "bogus")
    assert picked is not None and picked.option_id == "once"
