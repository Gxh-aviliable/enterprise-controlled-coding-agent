import pytest

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.workspace import get_user_workspace, set_current_user_id
from enterprise_agent.core.execution.changes import RestoreConflictError, restore_changes, task_changes
from enterprise_agent.core.execution.evidence import current_evidence, observe_file_mutation
from enterprise_agent.sandbox.executor import ExecutionRequest, execute


@pytest.fixture
def changes(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(settings, "AGENT_EXECUTOR", "local")
    set_current_user_id(777)
    root = get_user_workspace(777)
    token = current_evidence.set({"trace_id": "changes-test", "tool_call_id": "edit", "receipts": []})
    yield root
    current_evidence.reset(token)
    set_current_user_id(None)


def view(root):
    return task_changes(root, 777, "changes-test")


def restore(root, paths):
    return restore_changes(root, 777, "changes-test", paths, view(root)["version"])


def test_file_and_shell_changes_diff_and_restore_non_git(changes):
    root = changes
    (root / "a.txt").write_text("before\n")
    (root / "gone.txt").write_text("deleted\n")
    with observe_file_mutation():
        (root / "a.txt").write_text("after\n")
        (root / "gone.txt").unlink()
    execute(ExecutionRequest("echo added > new.txt", root, 777), lambda: False)
    result = view(root)
    assert {c["path"] for c in result["files"]} == {"a.txt", "gone.txt", "new.txt"}
    assert all(c["restorable"] for c in result["files"])
    assert "-before\n+after\n" in next(c["diff"] for c in result["files"] if c["path"] == "a.txt")
    done = restore(root, ["a.txt", "gone.txt", "new.txt"])
    assert done["status"] == "completed"
    assert (root / "a.txt").read_text() == "before\n"
    assert (root / "gone.txt").read_text() == "deleted\n"
    assert not (root / "new.txt").exists()


def test_conflict_preflights_every_file_before_any_write(changes):
    root = changes
    for p in ["a.txt", "b.txt"]:
        (root / p).write_text("old")
    with observe_file_mutation():
        for p in ["a.txt", "b.txt"]:
            (root / p).write_text("new")
    (root / "b.txt").write_text("human")
    with pytest.raises(RestoreConflictError, match="b.txt"):
        restore(root, ["a.txt", "b.txt"])
    assert (root / "a.txt").read_text() == "new"
    assert (root / "b.txt").read_text() == "human"
    assert task_changes(root, 778, "changes-test")["files"] == []


def test_partial_restore_records_applied_paths_and_refuses_blind_retry(changes, monkeypatch):
    from enterprise_agent.core.execution import changes as module

    root = changes
    with observe_file_mutation():
        (root / "a.txt").write_text("a")
        (root / "b.txt").write_text("b")
    original_restore = module._restore_file

    def interrupted(root, change, blobs):
        if change["path"] == "b.txt":
            raise OSError("injected interruption")
        return original_restore(root, change, blobs)

    monkeypatch.setattr(module, "_restore_file", interrupted)
    with pytest.raises(OSError, match="injected"):
        restore(root, ["a.txt", "b.txt"])
    assert view(root)["restore"]["applied_paths"] == ["a.txt"]
    assert view(root)["restore"]["status"] == "interrupted"
    with pytest.raises(RestoreConflictError, match="incomplete"):
        restore(root, ["b.txt"])


def test_binary_secret_large_and_symlink_are_not_restorable(changes, monkeypatch):
    from enterprise_agent.core.execution import changes as module

    root = changes
    monkeypatch.setattr(module, "MAX_FILE_BYTES", 32)
    (root / "binary.bin").write_bytes(b"\x00old")
    (root / "secret.txt").write_text("api_key=synthetic-secret")
    (root / "large.txt").write_text("a" * 33)
    with observe_file_mutation():
        for p in ["binary.bin", "secret.txt", "large.txt"]:
            (root / p).write_text("new")
        (root / "linked.txt").symlink_to(root / "secret.txt")
    assert all(not c["restorable"] for c in view(root)["files"])
    for p in root.parent.joinpath(".workspace-locks", "evidence").glob("*.snapshots.json"):
        assert "synthetic-secret" not in p.read_text()


def test_repeated_changes_and_intervening_user_edit(changes):
    root = changes
    (root / "a.txt").write_text("original")
    with observe_file_mutation():
        (root / "a.txt").write_text("first")
    (root / "a.txt").write_text("human")
    with observe_file_mutation():
        (root / "a.txt").write_text("second")
    assert view(root)["files"][0]["continuous"] is False
    with pytest.raises(RestoreConflictError):
        restore(root, ["a.txt"])


def test_expired_snapshots_do_not_restore_or_change_current_file(changes, monkeypatch):
    from enterprise_agent.core.execution import changes as module

    root = changes
    with observe_file_mutation():
        (root / "a.txt").write_text("keep")
    monkeypatch.setattr(module, "RETENTION_SECONDS", -1)
    assert not view(root)["files"][0]["restorable"]
    assert (root / "a.txt").read_text() == "keep"


def test_snapshot_storage_budget_includes_json_and_expiry_erases_text(changes, monkeypatch):
    from enterprise_agent.core.execution import changes as module

    root = changes
    monkeypatch.setattr(module, "MAX_TASK_BYTES", 500)
    monkeypatch.setattr(module, "MAX_TASK_FILES", 1)
    with observe_file_mutation():
        (root / "a.txt").write_text("\n" * 100)
        (root / "b.txt").write_text("out-of-file-budget")
    snapshot = module._snapshot_path(root, 777, "changes-test")
    assert snapshot.stat().st_size <= 500
    by_path = {c["path"]: c for c in view(root)["files"]}
    assert by_path["a.txt"]["restorable"]
    assert not by_path["b.txt"]["restorable"]
    monkeypatch.setattr(module, "RETENTION_SECONDS", -1)
    assert not view(root)["files"][0]["restorable"]
    assert '"blobs": {}' in snapshot.read_text()
    assert (root / "b.txt").read_text() == "out-of-file-budget"
