import json

import pytest

from enterprise_agent.observability.release import source_manifest


def test_snapshot_tracks_source_and_locks_without_credentials_or_history(tmp_path):
    (tmp_path / "enterprise_agent").mkdir()
    source = tmp_path / "enterprise_agent/app.py"
    source.write_text("x = 1")
    (tmp_path / "uv.lock").write_text("locked")
    (tmp_path / ".env").write_text("API_KEY=secret-canary")
    first = source_manifest(tmp_path)
    (tmp_path / "enterprise_agent/_build_manifest.json").write_text(json.dumps(first))
    assert source_manifest(tmp_path)["source_sha256"] == first["source_sha256"]
    assert "secret-canary" not in json.dumps(first)
    assert first["official"] is False
    assert first["qualification"] == "diagnostic-snapshot"
    assert first["database_revision"] is None
    source.write_text("x = 2")
    assert source_manifest(tmp_path)["source_sha256"] != first["source_sha256"]
    source.unlink()
    source.symlink_to(tmp_path / ".env")
    with pytest.raises(ValueError, match="symlinks"):
        source_manifest(tmp_path)
