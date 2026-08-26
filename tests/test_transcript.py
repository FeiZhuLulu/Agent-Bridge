import json

import pytest

from agent_bridge.paths import transcript_path
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


@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"])
def test_read_events_tail_keeps_event_at_exact_line_boundary(bridge_home, line_ending):
    first = {"type": "message_chunk", "data": {"text": "before"}}
    last = {"type": "message_chunk", "data": {"text": "\u4e2d\u6587"}}
    first_line = json.dumps(first, ensure_ascii=False).encode("utf-8") + line_ending
    last_line = json.dumps(last, ensure_ascii=False).encode("utf-8") + line_ending
    path = transcript_path("sess_boundary", bridge_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(first_line + last_line)

    assert read_events_tail("sess_boundary", bridge_home, max_bytes=len(last_line)) == [last]


def test_page_events_rejects_invalid_paging_parameters():
    events = [{"type": "message_chunk"}]

    with pytest.raises(ValueError, match="offset"):
        page_events(events, offset=-1)
    with pytest.raises(ValueError, match="limit"):
        page_events(events, limit=0)
