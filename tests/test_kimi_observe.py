import json

from agent_bridge.kimi_observe import kimi_wire_path, observe_kimi_session

SESSION = "session_6a1e7cd2-0ef8-4234-bb40-9cdebaa3f445"


def write_wire(home, records, session=SESSION, work_dir_key="wd_proj_beb7c1313a28"):
    path = home / "sessions" / work_dir_key / session / "agents" / "main" / "wire.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def test_finds_wire_under_any_work_dir_key(tmp_path):
    path = write_wire(tmp_path, [{"type": "turn.started"}])
    assert kimi_wire_path(SESSION, tmp_path) == path


def test_reads_model_and_effort_from_the_last_request(tmp_path):
    write_wire(
        tmp_path,
        [
            {"type": "llm.request", "modelAlias": "kimi-code/k3", "thinkingEffort": "low"},
            {"type": "llm.request", "modelAlias": "kimi-code/k3-256k", "thinkingEffort": "high"},
            {"type": "turn.ended", "reason": "completed"},
        ],
    )
    observed = observe_kimi_session(SESSION, tmp_path)
    assert observed == {"model": "kimi-code/k3-256k", "effort": "high", "failure": None}


def test_surfaces_the_failure_kimi_reports_as_end_turn(tmp_path):
    """The whole point: ACP said end_turn, wire.jsonl says 402."""
    write_wire(
        tmp_path,
        [
            {"type": "llm.request", "modelAlias": "kimi-code/k3-256k", "thinkingEffort": "high"},
            {
                "type": "turn.ended",
                "reason": "failed",
                "error": {"code": "provider.api_error", "message": "402 membership"},
            },
        ],
    )
    observed = observe_kimi_session(SESSION, tmp_path)
    assert observed["failure"] == "failed: provider.api_error: 402 membership"
    assert observed["model"] == "kimi-code/k3-256k"


def test_a_later_clean_turn_clears_an_earlier_failure(tmp_path):
    write_wire(
        tmp_path,
        [
            {"type": "turn.ended", "reason": "failed", "error": {"code": "x", "message": "y"}},
            {"type": "turn.ended", "reason": "completed"},
        ],
    )
    assert observe_kimi_session(SESSION, tmp_path)["failure"] is None


def test_reason_without_a_structured_error_still_reports(tmp_path):
    write_wire(tmp_path, [{"type": "turn.ended", "reason": "blocked"}])
    assert observe_kimi_session(SESSION, tmp_path)["failure"] == "blocked"


def test_only_failed_and_blocked_count_as_faults(tmp_path):
    """Kimi ends a turn with completed | cancelled | failed | blocked.

    `cancelled` is how cancel_task lands here, so calling it a failure would
    put a "the turn failed" warning on a task whose status is cancelled.
    """
    for reason, is_fault in (
        ("completed", False),
        ("cancelled", False),
        ("failed", True),
        ("blocked", True),
    ):
        write_wire(tmp_path, [{"type": "turn.ended", "reason": reason}])
        observed = observe_kimi_session(SESSION, tmp_path)
        assert (observed["failure"] is not None) is is_fault, reason


def test_a_cancelled_turn_still_reports_the_model(tmp_path):
    write_wire(
        tmp_path,
        [
            {"type": "llm.request", "modelAlias": "kimi-code/k3-256k", "thinkingEffort": "max"},
            {"type": "turn.ended", "reason": "cancelled", "durationMs": 120},
        ],
    )
    observed = observe_kimi_session(SESSION, tmp_path)
    assert observed == {"model": "kimi-code/k3-256k", "effort": "max", "failure": None}


def test_falls_back_to_model_when_there_is_no_alias(tmp_path):
    write_wire(tmp_path, [{"type": "llm.request", "model": "moonshot-v1-128k"}])
    assert observe_kimi_session(SESSION, tmp_path)["model"] == "moonshot-v1-128k"


def test_missing_session_answers_blank_instead_of_raising(tmp_path):
    blank = {"model": None, "effort": None, "failure": None}
    assert observe_kimi_session(None, tmp_path) == blank
    assert observe_kimi_session("session_absent", tmp_path) == blank


def test_truncated_and_garbage_lines_are_skipped(tmp_path):
    path = write_wire(tmp_path, [{"type": "turn.ended", "reason": "completed"}])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json at all\n")
        handle.write('{"type": "llm.request", "modelAlias": "kimi-code/k3"')
    observed = observe_kimi_session(SESSION, tmp_path)
    assert observed["failure"] is None
    assert observed["model"] is None


def test_only_the_tail_is_read(tmp_path, monkeypatch):
    from agent_bridge import kimi_observe

    # A window that opens mid-file drops its first, partial line. Shrink the
    # window so the padding below actually overflows it.
    monkeypatch.setattr(kimi_observe, "TAIL_BYTES", 2048)
    padding = [{"type": "llm.request", "modelAlias": "old", "pad": "x" * 400} for _ in range(20)]
    write_wire(
        tmp_path,
        [
            *padding,
            {"type": "llm.request", "modelAlias": "kimi-code/k3-256k", "thinkingEffort": "max"},
            {"type": "turn.ended", "reason": "completed"},
        ],
    )
    observed = observe_kimi_session(SESSION, tmp_path)
    assert observed["model"] == "kimi-code/k3-256k"
    assert observed["effort"] == "max"
