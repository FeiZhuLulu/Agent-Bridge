from agent_bridge.zcode_meta import (
    resolve_zcode_effort,
    resolve_zcode_model,
    zcode_model_candidates,
)


def test_effort_maps_onto_glm_thought_levels():
    offered = ["low", "high", "max"]
    assert resolve_zcode_effort("off", offered) == "low"
    assert resolve_zcode_effort("low", offered) == "low"
    assert resolve_zcode_effort("medium", offered) == "high"
    assert resolve_zcode_effort("high", offered) == "high"
    assert resolve_zcode_effort("max", offered) == "max"


def test_effort_is_none_when_the_model_advertises_nothing_comparable():
    assert resolve_zcode_effort("high", []) is None
    assert resolve_zcode_effort("high", None) is None
    assert resolve_zcode_effort(None, ["low"]) is None


def test_bare_model_id_resolves_when_unique():
    offered = [
        r"builtin:bigmodel-coding-plan\GLM-5.3",
        r"builtin:bigmodel-coding-plan\GLM-5-Turbo",
    ]
    assert resolve_zcode_model("GLM-5.3", offered) == offered[0]
    assert resolve_zcode_model(r"builtin:bigmodel-coding-plan\GLM-5.3", offered) == offered[0]
    assert (
        resolve_zcode_model("builtin:bigmodel-coding-plan/GLM-5.3", offered) == offered[0]
    )


def test_bare_model_id_is_ambiguous_when_two_providers_share_it():
    offered = [r"builtin:plan-a\GLM-5.3", r"builtin:plan-b\GLM-5.3"]
    assert zcode_model_candidates("GLM-5.3", offered) == offered
    assert resolve_zcode_model("GLM-5.3", offered) is None


def test_unknown_model_does_not_resolve():
    offered = [r"builtin:bigmodel-coding-plan\GLM-5.3"]
    assert resolve_zcode_model("GLM-9", offered) is None
