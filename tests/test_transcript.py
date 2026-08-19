from agent_bridge.transcript import append_event, page_events, read_events, read_events_tail


def test_append_and_page(bridge_home):
    for index in range(20):
        append_event("sess_a", "message_chunk", {"text": f"chunk-{index:02d}" * 40}, bridge_home)
    events = read_events("sess_a", bridge_home)
    assert len(events) == 20
    page = page_events(events, offset=0, limit=50, max_bytes=800)
    assert page["count"] < 20
    assert page["has_more"] is True
    page2 = page_events(events, offset=page["next_offset"], limit=50, max_bytes=800)
    assert page2["count"] >= 1


def test_kind_filter(bridge_home):
    append_event("sess_b", "message_chunk", {"text": "hi"}, bridge_home)
    append_event("sess_b", "thought_chunk", {"text": "hmm"}, bridge_home)
    events = read_events("sess_b", bridge_home)
    page = page_events(events, kinds=["thought_chunk"])
    assert page["total_matching"] == 1
    assert page["events"][0]["type"] == "thought_chunk"


def test_read_events_tail_returns_last_events_only(bridge_home):
    for index in range(200):
        append_event("sess_t", "message_chunk", {"text": f"chunk-{index:03d}" + "x" * 200}, bridge_home)
    tail = read_events_tail("sess_t", bridge_home, max_bytes=2000)
    full = read_events("sess_t", bridge_home)
    assert 0 < len(tail) < len(full)
    assert tail[-1] == full[-1]
    # Every tail event parses cleanly (no half-cut first record).
    assert all(event.get("type") == "message_chunk" for event in tail)


def test_read_events_tail_small_file_reads_everything(bridge_home):
    append_event("sess_s", "message_chunk", {"text": "only"}, bridge_home)
    assert read_events_tail("sess_s", bridge_home) == read_events("sess_s", bridge_home)
    assert read_events_tail("sess_missing", bridge_home) == []
