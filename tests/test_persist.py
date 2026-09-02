import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_bridge.persist import atomic_write_text, exclusive_file_lock


def test_atomic_write_text_replaces_and_leaves_no_tmp(tmp_path: Path):
    target = tmp_path / "note.txt"
    atomic_write_text(target, "one")
    atomic_write_text(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert list(tmp_path.glob("*.tmp*")) == []


def test_exclusive_file_lock_serializes_state_transactions(tmp_path: Path):
    lock_path = tmp_path / "state.lock"
    counter = tmp_path / "counter.txt"
    counter.write_text("0", encoding="utf-8")
    barrier = threading.Barrier(2)

    def increment() -> None:
        barrier.wait()
        with exclusive_file_lock(lock_path):
            value = int(counter.read_text(encoding="utf-8"))
            time.sleep(0.05)
            counter.write_text(str(value + 1), encoding="utf-8")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(increment) for _ in range(2)]
        for future in futures:
            future.result()

    assert counter.read_text(encoding="utf-8") == "2"
