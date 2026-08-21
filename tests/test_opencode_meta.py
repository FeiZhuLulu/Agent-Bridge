from agent_bridge.opencode_meta import resolve_opencode_effort


def test_effort_maps_onto_common_opencode_variants():
    # From OpenCode 1.18 ACP tests: default|low|medium|high. `max` is often
    # rejected, so Bridge max degrades to high.
    offered = ["default", "low", "medium", "high"]
    assert resolve_opencode_effort("off", offered) == "default"
    assert resolve_opencode_effort("low", offered) == "low"
    assert resolve_opencode_effort("medium", offered) == "medium"
    assert resolve_opencode_effort("high", offered) == "high"
    assert resolve_opencode_effort("max", offered) == "high"


def test_effort_prefers_an_exact_match():
    offered = ["off", "low", "medium", "high", "max"]
    for effort in ("off", "low", "medium", "high", "max"):
        assert resolve_opencode_effort(effort, offered) == effort


def test_no_variants_returns_none():
    assert resolve_opencode_effort("high", []) is None
    assert resolve_opencode_effort("high", ["turbo"]) is None
    assert resolve_opencode_effort(None, ["high"]) is None
