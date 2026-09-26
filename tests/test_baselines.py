from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from agent_bridge.baselines import Baseline, BaselineCorruptionError, BaselineStore


def make_record(
    baseline_id: str,
    cwd: Path | str,
    *,
    agent: str = "dsh",
    name: str = "baseline",
    description: str | None = None,
    native_session_id: str = "native-1",
    source_session_id: str = "sess-src",
    created_at: str | None = None,
) -> Baseline:
    fields: dict[str, object] = {
        "baseline_id": baseline_id,
        "name": name,
        "description": description,
        "agent": agent,
        "cwd": str(cwd),
        "native_session_id": native_session_id,
        "source_session_id": source_session_id,
    }
    if created_at is not None:
        fields["created_at"] = created_at
    return Baseline(**fields)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


def test_add_get_roundtrip_and_reload(tmp_path: Path, project: Path):
    home = tmp_path / "home"
    store = BaselineStore(home)
    record = make_record("base_0001", project, description="first saved fork")
    store.add(record)

    assert store.get("base_0001") == record
    assert (home / "baselines" / "records" / "base_0001.json").is_file()

    # A second instance reads the record on demand from disk.
    reopened = BaselineStore(home)
    assert reopened.get("base_0001") == record


def test_created_at_defaults_to_iso_timestamp(tmp_path: Path, project: Path):
    store = BaselineStore(tmp_path / "home")
    store.add(make_record("base_iso", project))
    loaded = store.get("base_iso")
    assert loaded.created_at
    assert datetime.fromisoformat(loaded.created_at)


def test_add_refuses_to_overwrite_immutable_id(tmp_path: Path, project: Path):
    home = tmp_path / "home"
    store = BaselineStore(home)
    store.add(make_record("base_dup", project, name="original", native_session_id="native-a"))

    with pytest.raises(FileExistsError):
        store.add(make_record("base_dup", project, name="replacement", native_session_id="native-b"))

    assert store.get("base_dup").name == "original"
    # Immutability holds across independent instances too.
    assert BaselineStore(home).get("base_dup").native_session_id == "native-a"


def test_get_missing_id_raises_keyerror(tmp_path: Path):
    store = BaselineStore(tmp_path / "home")
    with pytest.raises(KeyError):
        store.get("base_absent")


def test_list_filters_by_scope_and_sorts_by_created_at(tmp_path: Path):
    home = tmp_path / "home"
    project_a = tmp_path / "alpha"
    project_b = tmp_path / "beta"
    store = BaselineStore(home)
    store.add(make_record("base_late", project_a, created_at="2024-01-02T00:00:00+00:00"))
    store.add(make_record("base_early", project_a, created_at="2024-01-01T00:00:00+00:00"))
    store.add(make_record("base_other_project", project_b))
    store.add(make_record("base_other_agent", project_a, agent="claude"))

    listed = store.list(project_a, "dsh")
    assert [item.baseline_id for item in listed] == ["base_early", "base_late"]
    assert [item.baseline_id for item in store.list(project_b, "dsh")] == ["base_other_project"]
    assert [item.baseline_id for item in store.list(project_a, "claude")] == ["base_other_agent"]


def test_scope_uses_canonical_cwd(tmp_path: Path, project: Path):
    store = BaselineStore(tmp_path / "home")
    store.add(make_record("base_canon", project))

    detoured = str(project / ".." / project.name) + os.sep
    assert [item.baseline_id for item in store.list(detoured, "dsh")] == ["base_canon"]

    # A normcase-different spelling of the same directory is the same scope.
    spelled = os.path.normcase(str(project))
    store.set_default(os.path.normcase(detoured), "dsh", "base_canon")
    assert store.get_default(spelled, "dsh") == "base_canon"


def test_default_roundtrip_and_clear_retains_records(tmp_path: Path, project: Path):
    store = BaselineStore(tmp_path / "home")
    store.add(make_record("base_one", project))
    store.add(make_record("base_two", project))

    assert store.get_default(project, "dsh") is None
    store.set_default(project, "dsh", "base_two")
    assert store.get_default(project, "dsh") == "base_two"

    store.set_default(project, "dsh", None)
    assert store.get_default(project, "dsh") is None
    assert [item.baseline_id for item in store.list(project, "dsh")] == ["base_one", "base_two"]


def test_defaults_are_persisted_per_scope_across_instances(tmp_path: Path):
    home = tmp_path / "home"
    project_a = tmp_path / "alpha"
    project_b = tmp_path / "beta"
    store_a = BaselineStore(home)
    store_b = BaselineStore(home)
    store_a.add(make_record("base_a", project_a))
    store_b.add(make_record("base_b", project_b))

    store_a.set_default(project_a, "dsh", "base_a")
    store_b.set_default(project_b, "dsh", "base_b")

    # Neither instance's selector overwrote the other scope's file.
    assert store_a.get_default(project_a, "dsh") == "base_a"
    assert store_b.get_default(project_b, "dsh") == "base_b"
    assert store_a.get_default(project_b, "dsh") == "base_b"
    assert store_b.get_default(project_a, "dsh") == "base_a"

    store_a.set_default(project_a, "dsh", None)
    assert store_b.get_default(project_a, "dsh") is None
    assert BaselineStore(home).get_default(project_b, "dsh") == "base_b"
    assert len(list((home / "baselines" / "defaults").glob("*.json"))) == 2


def test_add_does_not_touch_other_scope_default(tmp_path: Path):
    home = tmp_path / "home"
    project_a = tmp_path / "alpha"
    project_b = tmp_path / "beta"
    store = BaselineStore(home)
    store.add(make_record("base_a", project_a))
    store.set_default(project_a, "dsh", "base_a")

    store.add(make_record("base_b", project_b, agent="claude"))
    store.set_default(project_b, "claude", "base_b")

    assert store.get_default(project_a, "dsh") == "base_a"
    assert store.get_default(project_b, "claude") == "base_b"


def test_set_default_rejects_missing_and_foreign_scope(tmp_path: Path):
    home = tmp_path / "home"
    project_a = tmp_path / "alpha"
    project_b = tmp_path / "beta"
    store = BaselineStore(home)
    store.add(make_record("base_a", project_a))

    with pytest.raises(KeyError):
        store.set_default(project_a, "dsh", "base_missing")
    with pytest.raises(ValueError):
        store.set_default(project_b, "dsh", "base_a")
    with pytest.raises(ValueError):
        store.set_default(project_a, "claude", "base_a")
    assert store.get_default(project_b, "dsh") is None


@pytest.mark.parametrize(
    "bad_id",
    ["", "..", "../evil", "a/b", "a\\b", ".hidden", "base id", "base..x", "base\n", "x" * 200],
)
def test_invalid_ids_are_rejected_before_touching_filenames(tmp_path: Path, project: Path, bad_id: str):
    store = BaselineStore(tmp_path / "home")
    with pytest.raises(ValueError):
        store.add(make_record(bad_id, project))
    with pytest.raises(ValueError):
        store.get(bad_id)
    with pytest.raises(ValueError):
        store.set_default(project, "dsh", bad_id)
    assert not (tmp_path / "home" / "baselines").exists()


@pytest.mark.parametrize("field", ["name", "native_session_id", "source_session_id"])
def test_add_rejects_blank_required_fields(tmp_path: Path, project: Path, field: str):
    store = BaselineStore(tmp_path / "home")
    record = make_record("base_blank", project)
    setattr(record, field, "   ")
    with pytest.raises(ValueError):
        store.add(record)


def test_corrupt_record_file_is_not_read_as_empty(tmp_path: Path, project: Path):
    home = tmp_path / "home"
    records = home / "baselines" / "records"
    records.mkdir(parents=True)
    (records / "base_bad.json").write_text("{not valid json", encoding="utf-8")
    store = BaselineStore(home)

    with pytest.raises(BaselineCorruptionError):
        store.get("base_bad")
    with pytest.raises(BaselineCorruptionError):
        store.list(project, "dsh")


def test_null_record_file_is_corruption_not_missing(tmp_path: Path):
    home = tmp_path / "home"
    records = home / "baselines" / "records"
    records.mkdir(parents=True)
    (records / "base_null.json").write_text("null", encoding="utf-8")
    store = BaselineStore(home)

    with pytest.raises(BaselineCorruptionError):
        store.get("base_null")


def test_corrupt_default_selector_is_not_read_as_none(tmp_path: Path, project: Path):
    home = tmp_path / "home"
    store = BaselineStore(home)
    store.add(make_record("base_ok", project))
    store.set_default(project, "dsh", "base_ok")

    selector = next((home / "baselines" / "defaults").glob("*.json"))
    selector.write_text(json.dumps({"baseline_id": 17}), encoding="utf-8")
    with pytest.raises(BaselineCorruptionError):
        store.get_default(project, "dsh")

    selector.write_text("{broken", encoding="utf-8")
    with pytest.raises(BaselineCorruptionError):
        BaselineStore(home).get_default(project, "dsh")
