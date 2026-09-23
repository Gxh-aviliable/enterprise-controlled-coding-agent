import os

import pytest

from enterprise_agent.sandbox.engine import SandboxError
from enterprise_agent.sandbox.workspace import inventory, publish, snapshot

LIMITS = (10000, 100)


def test_publish_accepts_admin_base_alias_but_rejects_workspace_links(tmp_path):
    base, alias, stage = tmp_path / "base", tmp_path / "alias", tmp_path / "stage"
    (base / "user").mkdir(parents=True)
    alias.symlink_to(base, target_is_directory=True)
    stage.mkdir()
    (stage / "new.txt").write_text("published")
    assert publish(stage, alias / "user", {}, LIMITS) == ["new.txt"]
    assert (base / "user" / "new.txt").read_text() == "published"
    linked_user = base / "linked-user"
    linked_user.symlink_to(base / "user", target_is_directory=True)
    with pytest.raises(SandboxError, match="symlink"):
        publish(stage, linked_user, {}, LIMITS)
    (stage / "nested").mkdir()
    (stage / "nested" / "new.txt").write_text("blocked")
    (base / "user" / "nested").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(SandboxError, match="authorization|symlink"):
        publish(stage, base / "user", {"new.txt": inventory(stage, *LIMITS)["new.txt"]}, LIMITS)
    assert not (tmp_path / "new.txt").exists()


def test_partial_publication_retains_applied_paths(monkeypatch, tmp_path):
    source, stage = tmp_path / "user", tmp_path / "stage"
    source.mkdir()
    stage.mkdir()
    (stage / "a.py").write_text("first")
    (stage / "b.py").write_text("second")
    replace = os.replace

    def fail_second(src, dst):
        if dst.name == "b.py":
            raise OSError("injected disk failure")
        return replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_second)
    applied = []
    with pytest.raises(OSError, match="injected"):
        publish(stage, source, {}, LIMITS, published_paths=applied)
    assert applied == ["a.py"]
    assert (source / "a.py").read_text() == "first"
    assert not (source / "b.py").exists()


def test_filtered_snapshot_and_conflict_does_not_overwrite(tmp_path):
    src, stage = tmp_path / "user", tmp_path / "stage"
    src.mkdir()
    stage.mkdir()
    (src / "main.py").write_text("old")
    (src / ".env").write_text("secret")
    (src / ".agent").mkdir()
    (src / ".agent" / "secret").write_text("secret")
    baseline = snapshot(src, stage, LIMITS)
    assert sorted(stage.iterdir()) == [stage / "main.py"]
    (stage / "main.py").write_text("agent")
    (stage / "new.py").write_text("new")
    (src / "main.py").write_text("human")
    with pytest.raises(SandboxError, match="conflict"):
        publish(stage, src, baseline, LIMITS)
    assert (src / "main.py").read_text() == "human"
    assert not (src / "new.py").exists()


def test_snapshot_rejects_file_symlinks_hardlinks_and_budget(tmp_path):
    (tmp_path / "safe").write_text("ok")
    os.link(tmp_path / "safe", tmp_path / "link")
    with pytest.raises(SandboxError, match="link"):
        inventory(tmp_path, *LIMITS)
    (tmp_path / "link").unlink()
    (tmp_path / "link").symlink_to("/etc/passwd")
    with pytest.raises(SandboxError, match="link"):
        inventory(tmp_path, *LIMITS)
    (tmp_path / "link").unlink()
    with pytest.raises(SandboxError, match="budget"):
        inventory(tmp_path, 1, 100)


def test_publish_rejects_container_generated_link(tmp_path):
    src, stage = tmp_path / "user", tmp_path / "stage"
    src.mkdir()
    stage.mkdir()
    (stage / "escape").symlink_to("/etc/passwd")
    with pytest.raises(SandboxError, match="link"):
        publish(stage, src, {}, LIMITS)
    assert not (src / "escape").exists()


def test_parent_conflict_rejected_before_any_publish(tmp_path):
    src, stage = tmp_path / "user", tmp_path / "stage"
    src.mkdir()
    stage.mkdir()
    (stage / "a.txt").write_text("new")
    (stage / "z").mkdir()
    (stage / "z" / "child").write_text("new")
    (src / "z").write_text("human-file")
    with pytest.raises(SandboxError, match="conflict"):
        publish(stage, src, {}, LIMITS)
    assert not (src / "a.txt").exists()
    assert (src / "z").read_text() == "human-file"


def test_cleanup_chmod_zero_does_not_follow_symlink(tmp_path):
    from enterprise_agent.sandbox.storage import remove_snapshot

    outside, stage = tmp_path / "outside", tmp_path / "stage"
    outside.mkdir(mode=0o755)
    (outside / "keep").write_text("safe")
    stage.mkdir()
    (stage / "child").mkdir()
    (stage / "child" / "link").symlink_to(outside, target_is_directory=True)
    (stage / "child").chmod(0)
    stage.chmod(0)
    remove_snapshot(stage)
    assert (outside / "keep").read_text() == "safe"
    assert outside.stat().st_mode & 0o777 == 0o755
    assert not stage.exists()


@pytest.mark.parametrize("persistent", [False, True])
def test_snapshot_cleanup_retries_transient_detach_permission_only(tmp_path, monkeypatch, persistent):
    import shutil

    from enterprise_agent.sandbox.storage import remove_snapshot

    stage = tmp_path / "owned-snapshot"
    stage.mkdir()
    (stage / "node_modules").mkdir()
    real_rmtree = shutil.rmtree
    calls = []

    def delayed_detach(path):
        calls.append(path)
        if persistent or len(calls) == 1:
            raise PermissionError("nested bind detach pending")
        real_rmtree(path)

    monkeypatch.setattr("enterprise_agent.sandbox.storage.shutil.rmtree", delayed_detach)
    if persistent:
        with pytest.raises(PermissionError):
            remove_snapshot(stage)
        assert len(calls) == 3 and stage.exists()
    else:
        remove_snapshot(stage)
        assert len(calls) == 2 and not stage.exists()
