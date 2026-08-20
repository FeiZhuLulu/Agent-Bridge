from agent_bridge.kimi_meta import (
    KIMI_MODE_YOLO,
    config_option_values,
    resolve_kimi_thinking,
)

# Verbatim from a live `kimi acp` session/new on kimi-code/k3-256k.
LIVE_OPTIONS = [
    {
        "currentValue": "kimi-code/k3-256k",
        "options": [
            {"value": "kimi-code/kimi-for-coding", "name": "K2.7 Coding"},
            {"value": "kimi-code/kimi-for-coding-highspeed", "name": "K2.7 Coding Highspeed"},
            {"value": "kimi-code/k3", "name": "K3"},
            {"value": "kimi-code/k3-256k", "name": "K3-256k"},
        ],
        "id": "model",
        "name": "Model",
        "category": "model",
        "type": "select",
    },
    {
        "currentValue": "high",
        "options": [
            {"value": "low", "name": "Thinking Low"},
            {"value": "high", "name": "Thinking High"},
            {"value": "max", "name": "Thinking Max"},
        ],
        "id": "thinking",
        "name": "Thinking",
        "category": "thought_level",
        "type": "select",
    },
]


def test_reads_live_option_snapshot():
    current, offered = config_option_values(LIVE_OPTIONS, "model")
    assert current == "kimi-code/k3-256k"
    assert "kimi-code/k3" in offered
    assert len(offered) == 4

    current, offered = config_option_values(LIVE_OPTIONS, "thinking")
    assert current == "high"
    assert offered == ["low", "high", "max"]


def test_unknown_option_id_is_empty():
    assert config_option_values(LIVE_OPTIONS, "nope") == (None, [])
    assert config_option_values(None, "thinking") == (None, [])


def test_reads_grouped_options():
    """ACP types `options` as flat *or* grouped; both must yield values."""
    grouped = [
        {
            "currentValue": "b",
            "id": "model",
            "options": [
                {"group": "moonshot", "name": "Moonshot", "options": [{"value": "a"}, {"value": "b"}]},
            ],
        }
    ]
    assert config_option_values(grouped, "model") == ("b", ["a", "b"])


def test_pydantic_models_are_accepted():
    """The adapter passes SDK models straight through, not dicts."""
    from acp.schema import SessionConfigOptionSelect, SessionConfigSelectOption

    option = SessionConfigOptionSelect(
        currentValue="low",
        id="thinking",
        name="Thinking",
        type="select",
        options=[SessionConfigSelectOption(value="low", name="Low")],
    )
    assert config_option_values([option], "thinking") == ("low", ["low"])


def test_effort_maps_onto_the_levels_k3_offers():
    offered = ["low", "high", "max"]
    # k3-256k publishes no `off` and no `medium`, so both degrade to a
    # neighbour instead of being dropped.
    assert resolve_kimi_thinking("off", offered) == "low"
    assert resolve_kimi_thinking("low", offered) == "low"
    assert resolve_kimi_thinking("medium", offered) == "high"
    assert resolve_kimi_thinking("high", offered) == "high"
    assert resolve_kimi_thinking("max", offered) == "max"


def test_effort_prefers_an_exact_match():
    offered = ["off", "low", "medium", "high", "max"]
    for effort in ("off", "low", "medium", "high", "max"):
        assert resolve_kimi_thinking(effort, offered) == effort


def test_effort_maps_onto_a_boolean_vocabulary():
    assert resolve_kimi_thinking("high", ["off", "on"]) == "on"
    assert resolve_kimi_thinking("off", ["off", "on"]) == "off"


def test_no_common_ground_returns_none():
    assert resolve_kimi_thinking("high", []) is None
    assert resolve_kimi_thinking("high", ["turbo"]) is None
    assert resolve_kimi_thinking(None, ["high"]) is None


def test_yolo_is_the_headless_mode():
    assert KIMI_MODE_YOLO == "yolo"
