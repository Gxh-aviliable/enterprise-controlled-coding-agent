"""Recoverable, workspace-isolated tool-output artifact tests."""

import hashlib
import json
import os

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tool_artifacts import (
    ToolArtifactStore,
    format_tool_output,
)
from enterprise_agent.core.agent.tools.context_tools import read_tool_artifact
from enterprise_agent.core.agent.tools.workspace import set_current_user_id


def test_artifact_is_atomic_private_redacted_and_path_safe(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(settings, "TOOL_ARTIFACT_MAX_CHARS", 10_000)
    raw = "begin api_key=super-secret-value end\nFACT_FAILURE=expected 2 got 8"

    receipt = ToolArtifactStore(user_id=7).save(
        raw,
        trace_id="../../trace escape",
        tool_call_id="../call/escape",
    )

    workspace = (tmp_path / "user_7").resolve()
    artifact = (workspace / receipt.path).resolve()
    assert artifact.is_relative_to(workspace)
    assert artifact.is_file()
    stored = artifact.read_text(encoding="utf-8")
    assert "super-secret-value" not in stored
    assert "[REDACTED]" in stored
    assert "FACT_FAILURE=expected 2 got 8" in stored
    assert receipt.redacted is True
    assert receipt.sha256 == hashlib.sha256(stored.encode("utf-8")).hexdigest()
    assert not list(artifact.parent.glob("*.tmp"))
    if os.name != "nt":
        assert artifact.stat().st_mode & 0o777 == 0o600


def test_artifacts_with_same_ids_are_isolated_by_user(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    first = ToolArtifactStore(user_id=1).save(
        "user one evidence",
        trace_id="same-trace",
        tool_call_id="same-call",
    )
    second = ToolArtifactStore(user_id=2).save(
        "user two evidence",
        trace_id="same-trace",
        tool_call_id="same-call",
    )

    assert (tmp_path / "user_1" / first.path).read_text() == "user one evidence"
    assert (tmp_path / "user_2" / second.path).read_text() == "user two evidence"


def test_same_call_id_never_overwrites_different_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    store = ToolArtifactStore(user_id=8)

    first = store.save("first evidence", trace_id="trace", tool_call_id="same-call")
    second = store.save("second evidence", trace_id="trace", tool_call_id="same-call")

    assert first.path != second.path
    assert (tmp_path / "user_8" / first.path).read_text() == "first evidence"
    assert (tmp_path / "user_8" / second.path).read_text() == "second evidence"
    assert store.validate_receipt(first.to_dict()) == (True, None)
    assert store.validate_receipt(second.to_dict()) == (True, None)


def test_receipt_validation_rejects_tampering_and_outside_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    store = ToolArtifactStore(user_id=9)
    receipt = store.save("trusted evidence", trace_id="trace", tool_call_id="call")
    artifact = tmp_path / "user_9" / receipt.path
    artifact.write_text("tampered", encoding="utf-8")

    valid, reason = store.validate_receipt(receipt.to_dict())
    assert valid is False
    assert reason == "artifact_hash_mismatch"
    outside = receipt.to_dict()
    outside["path"] = "unrelated.txt"
    assert store.validate_receipt(outside) == (False, "invalid_artifact_path")


def test_artifact_range_read_is_bounded_and_rejects_traversal(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    store = ToolArtifactStore(user_id=10)
    receipt = store.save("0123456789", trace_id="trace", tool_call_id="call")

    first = store.read_range(
        receipt.path,
        expected_sha256=receipt.sha256,
        offset_bytes=0,
        limit_bytes=4,
    )
    second = store.read_range(
        receipt.path,
        expected_sha256=receipt.sha256,
        offset_bytes=first["next_offset_bytes"],
        limit_bytes=4,
    )
    assert first["content"] == "0123"
    assert first["eof"] is False
    assert second["content"] == "4567"
    assert second["sha256"] == receipt.sha256

    try:
        store.read_range(
            "../../outside",
            expected_sha256=receipt.sha256,
            offset_bytes=0,
            limit_bytes=4,
        )
    except ValueError as exc:
        assert str(exc) == "invalid_artifact_path"
    else:
        raise AssertionError("artifact traversal should be rejected")


def test_read_tool_artifact_pages_current_user_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    set_current_user_id(11)
    try:
        receipt = ToolArtifactStore(user_id=11).save(
            "0123456789",
            trace_id="trace-page",
            tool_call_id="call-page",
        )

        first = json.loads(read_tool_artifact.invoke({
            "path": receipt.path,
            "sha256": receipt.sha256,
            "offset_bytes": 0,
            "limit_bytes": 4,
        }))
        second = json.loads(read_tool_artifact.invoke({
            "path": receipt.path,
            "sha256": receipt.sha256,
            "offset_bytes": first["next_offset_bytes"],
            "limit_bytes": 4,
        }))
        final = json.loads(read_tool_artifact.invoke({
            "path": receipt.path,
            "sha256": receipt.sha256,
            "offset_bytes": second["next_offset_bytes"],
            "limit_bytes": 4,
        }))

        assert first == {
            "path": receipt.path,
            "offset_bytes": 0,
            "returned_bytes": 4,
            "next_offset_bytes": 4,
            "total_bytes": 10,
            "eof": False,
            "sha256": receipt.sha256,
            "content": "0123",
        }
        assert second["content"] == "4567"
        assert second["next_offset_bytes"] == 8
        assert second["eof"] is False
        assert final["content"] == "89"
        assert final["returned_bytes"] == 2
        assert final["next_offset_bytes"] == 10
        assert final["eof"] is True
    finally:
        set_current_user_id(None)


def test_read_tool_artifact_rejects_outside_and_traversal_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    outside = tmp_path / "outside.txt"
    outside.write_text("must not be readable", encoding="utf-8")
    set_current_user_id(12)
    try:
        traversal = read_tool_artifact.invoke({
            "path": "../../outside.txt",
            "sha256": "0" * 64,
            "offset_bytes": 0,
            "limit_bytes": 100,
        })
        absolute = read_tool_artifact.invoke({
            "path": str(outside),
            "sha256": "0" * 64,
            "offset_bytes": 0,
            "limit_bytes": 100,
        })
        malformed_root = read_tool_artifact.invoke({
            "path": ".agent/tool-artifacts/trace/nested/file.txt",
            "sha256": "0" * 64,
            "offset_bytes": 0,
            "limit_bytes": 100,
        })

        assert traversal == "Error: Artifact read rejected (invalid_artifact_path)"
        assert absolute == "Error: Artifact read rejected (invalid_artifact_path)"
        assert malformed_root == "Error: Artifact read rejected (invalid_artifact_path)"
        assert outside.read_text(encoding="utf-8") == "must not be readable"
    finally:
        set_current_user_id(None)


def test_read_tool_artifact_uses_current_user_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    receipt = ToolArtifactStore(user_id=21).save(
        "private user 21 evidence",
        trace_id="shared-trace",
        tool_call_id="shared-call",
    )

    set_current_user_id(22)
    try:
        foreign_result = read_tool_artifact.invoke({
            "path": receipt.path,
            "sha256": receipt.sha256,
        })
    finally:
        set_current_user_id(None)

    set_current_user_id(21)
    try:
        owned_result = json.loads(read_tool_artifact.invoke({
            "path": receipt.path,
            "sha256": receipt.sha256,
        }))
    finally:
        set_current_user_id(None)

    assert foreign_result == "Error: Artifact read rejected (artifact_not_found)"
    assert owned_result["content"] == "private user 21 evidence"
    assert owned_result["path"] == receipt.path


def test_artifact_read_rejects_tampering_and_preserves_utf8_pages(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    store = ToolArtifactStore(user_id=23)
    receipt = store.save("你好世界", trace_id="utf8-trace", tool_call_id="utf8-call")

    offset = 0
    pages = []
    while True:
        page = store.read_range(
            receipt.path,
            expected_sha256=receipt.sha256,
            offset_bytes=offset,
            limit_bytes=1,
        )
        pages.append(page["content"])
        if page["eof"]:
            break
        assert page["next_offset_bytes"] > offset
        offset = page["next_offset_bytes"]

    assert "".join(pages) == "你好世界"
    artifact = tmp_path / "user_23" / receipt.path
    artifact.write_text("篡改", encoding="utf-8")
    try:
        store.read_range(
            receipt.path,
            expected_sha256=receipt.sha256,
            offset_bytes=0,
            limit_bytes=10,
        )
    except ValueError as exc:
        assert str(exc) == "artifact_hash_mismatch"
    else:
        raise AssertionError("tampered evidence must not be returned")


def test_receipt_validation_handles_legacy_invalid_size(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    store = ToolArtifactStore(user_id=24)
    receipt = store.save("evidence", trace_id="trace", tool_call_id="call").to_dict()
    receipt["stored_bytes"] = "not-an-int"

    assert store.validate_receipt(receipt) == (False, "artifact_invalid_size")

    receipt = store.save("evidence 2", trace_id="trace", tool_call_id="call-2").to_dict()
    receipt.pop("original_chars")
    assert store.validate_receipt(receipt) == (False, "artifact_invalid_metadata")


def test_artifact_capture_and_model_preview_have_independent_caps(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(settings, "TOOL_ARTIFACT_MAX_CHARS", 800)
    monkeypatch.setattr(settings, "TOOL_OUTPUT_MAX_CHARS", 400)
    raw = "HEAD_FACT\n" + ("x" * 2000) + "\nTAIL_FACT"

    receipt = ToolArtifactStore(user_id=3).save(
        raw,
        trace_id="trace-cap",
        tool_call_id="call-cap",
    )
    preview, model_truncated = format_tool_output(
        raw,
        receipt=receipt,
        status="error",
        error_code="nonzero_exit",
        exit_code=2,
    )

    stored = (tmp_path / "user_3" / receipt.path).read_text()
    assert receipt.source_truncated is True
    assert "HEAD_FACT" in stored
    assert "TAIL_FACT" in stored
    assert model_truncated is True
    assert len(preview) <= settings.TOOL_OUTPUT_MAX_CHARS
    assert "status=error" in preview
    assert "exit_code=2" in preview
    assert receipt.path in preview


def test_symlinked_artifact_directory_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    workspace = tmp_path / "user_4"
    workspace.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / ".agent").symlink_to(outside, target_is_directory=True)

    try:
        ToolArtifactStore(user_id=4).save(
            "must stay inside",
            trace_id="trace",
            tool_call_id="call",
        )
    except ValueError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("symlinked artifact root should be rejected")
