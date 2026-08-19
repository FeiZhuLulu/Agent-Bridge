from agent_bridge.server import mcp


def test_nine_tools_registered():
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
        "wait_task",
    ]
