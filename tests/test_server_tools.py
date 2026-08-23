from agent_bridge.server import INSTRUCTIONS, mcp


def test_ten_tools_registered():
    names = sorted(mcp._tool_manager._tools)
    assert names == [
        "cancel_task",
        "check_task",
        "dispatch_task",
        "end_session",
        "get_result",
        "get_transcript",
        "list_agents",
        "list_sessions",
        "set_preferences",
        "wait_task",
    ]


def test_handshake_instructions_carry_hard_rules():
    assert mcp.instructions == INSTRUCTIONS
    for phrase in (
        "dispatch_task.cwd",
        "wait_task",
        "coordinator.mode",
        "dispatch_enabled",
        "runtime_context",
        "cancel_task",
        "end_session",
    ):
        assert phrase in INSTRUCTIONS
