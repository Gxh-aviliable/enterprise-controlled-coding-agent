import json
import time
from datetime import datetime, timezone

import pytest

from enterprise_agent.core.agent.tools.workspace import get_user_workspace
from enterprise_agent.core.execution.changes import _snapshot_path, capture_text
from enterprise_agent.core.execution.evidence import save_receipt, workspace_state
from enterprise_agent.observability import trace_store
from enterprise_agent.observability.storage_governance import (
    backup_records,
    maintain_storage,
    restore_backup,
    verify_backup,
)


def create(store, name, terminal=True):
    store.start_trace(user_id=711, trace_id=name, session_id="synthetic", request_summary="synthetic")
    if terminal:
        trace = store.finish_trace(user_id=711, trace_id=name, status="failed")
        trace["finished_at"] = datetime.fromtimestamp(time.time() - 31 * 86400, timezone.utc).isoformat()
        store._write(711, trace)


def test_trusted_boundary_legacy_read_and_safe_retention(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path / "workspace"))
    store = trace_store.get_trace_store()
    create(store, "completed")
    root = get_user_workspace(711)
    assert not store._path(711, "completed").is_relative_to(root)
    legacy = root / ".agent/traces/legacy.json"
    legacy.parent.mkdir(parents=True)
    data = store.get_trace(711, "completed")
    data["trace_id"] = "legacy"
    data["request_summary"] = "password=synthetic-secret"
    legacy.write_text(json.dumps(data))
    assert store.get_trace(711, "legacy")["storage_trust"] == "legacy_unverified"
    assert "synthetic-secret" not in store.get_trace(711, "legacy")["request_summary"]
    assert store.aggregate_metrics(711)['task_count'] == 1
    create(store, "running", terminal=False)
    create(store, "uncertain")
    save_receipt({"trace_id": "uncertain", "execution_id": "one", "phase": "running"}, 711, root)
    (root / "a.txt").write_text("synthetic")
    capture_text(root, 711, "running", workspace_state(root)["files"])
    path = _snapshot_path(root, 711, "running")
    snapshot = json.loads(path.read_text())
    snapshot["created_at"] = time.time() - 8 * 86400
    path.write_text(json.dumps(snapshot))
    result = maintain_storage()
    assert result["deleted_tasks"] == 1 and result["held_uncertain_tasks"] == 1
    assert result["expired_snapshots"] == 1 and json.loads(path.read_text())["blobs"] == {}
    assert legacy.exists()
    assert store.get_trace(711, "uncertain")["status"] == "failed"
    assert store.get_trace(711, "running")["status"] == "pending"
    with pytest.raises(FileNotFoundError):
        store.get_trace(711, "completed")


def test_backup_restore_checksums_and_no_overwrite(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path / "workspace"))
    store = trace_store.get_trace_store()
    create(store, "one")
    backup = tmp_path / "backup"
    target = tmp_path / "restored"
    manifest = backup_records(backup)
    assert restore_backup(backup, target)["restored_files"] == len(manifest["files"])
    for rel in manifest["files"]:
        assert (backup / rel).read_bytes() == (target / rel).read_bytes()
    with pytest.raises(FileExistsError):
        restore_backup(backup, target)
    (backup / next(iter(manifest["files"]))).write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        verify_backup(backup)


def test_trace_event_cap_preserves_metrics(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(trace_store, "MAX_TRACE_EVENTS", 3)
    store = trace_store.TraceStore()
    create(store, "cap", terminal=False)
    for _ in range(5):
        store.record_event(user_id=711, trace_id="cap", event_type="tool", name="read_file")
    trace = store.get_trace(711, "cap")
    assert len(trace["events"]) == 3 and trace["events_dropped"] == 3
    assert trace["metrics"]["tool_calls"] == 5


def test_legacy_links_and_forged_owner_are_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path / "workspaces"))
    store = trace_store.TraceStore()
    create(store, "original")
    target = get_user_workspace(712) / ".agent/traces"
    target.mkdir(parents=True)
    (target / "original.json").write_text(store._path(711, "original").read_text())
    with pytest.raises(FileNotFoundError):
        store.get_trace(712, "original")
    (target / "link.json").symlink_to(store._path(711, "original"))
    with pytest.raises(OSError):
        store.get_trace(712, "link")
