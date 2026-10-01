from agent_bridge.minimax_meta import (
    encode_minimax_model,
    minimax_model_labels,
    parse_minimax_model_value,
    resolve_minimax_effort,
    resolve_minimax_model,
)


def test_model_slug_round_trips_through_the_acp_encoding():
    encoded = encode_minimax_model("custom_provider:work", "deep-reasoner-1")
    assert encoded == "m:custom_provider%3Awork:deep-reasoner-1:u"
    assert parse_minimax_model_value(encoded) == ("custom_provider:work", "deep-reasoner-1", None)

    variant = encode_minimax_model("minimax", "MiniMax-M3", "1M")
    assert variant == "m:minimax:MiniMax-M3:v:1M"
    assert parse_minimax_model_value(variant) == ("minimax", "MiniMax-M3", "1M")


def test_human_slug_resolves_to_the_advertised_value():
    plain = encode_minimax_model("minimax", "MiniMax-M2.7")
    variant = encode_minimax_model("minimax", "MiniMax-M3", "1M")
    offered = [plain, variant]
    assert resolve_minimax_model("minimax/MiniMax-M2.7", offered) == plain
    assert resolve_minimax_model("minimax/MiniMax-M3#1M", offered) == variant
    assert resolve_minimax_model(plain, offered) == plain
    assert resolve_minimax_model("other/nope", offered) is None
    assert minimax_model_labels(offered) == ["minimax/MiniMax-M2.7", "minimax/MiniMax-M3#1M"]


def test_effort_maps_onto_minimax_thinking_levels():
    offered = ["default", "low", "medium", "high", "xhigh", "max"]
    assert resolve_minimax_effort("off", offered) == "default"
    assert resolve_minimax_effort("low", offered) == "low"
    assert resolve_minimax_effort("medium", offered) == "medium"
    assert resolve_minimax_effort("high", offered) == "high"
    assert resolve_minimax_effort("max", offered) == "max"
    assert resolve_minimax_effort("high", []) is None
