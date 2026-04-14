import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from ifetch.exporters.index_manager import UploadIndexManager  # noqa: E402


def test_file_needs_upload_updates_mtime_when_checksum_matches(tmp_path):
    index_file = tmp_path / "index.json"
    manager = UploadIndexManager(index_file=str(index_file), autosave_interval=0)
    manager.add_file_entry("docs/report.txt", 10, 100.0, "abc", "file-1")

    needs_upload = manager.file_needs_upload(
        "docs/report.txt",
        current_size=10,
        current_mtime=200.0,
        current_checksum="abc",
    )

    assert needs_upload is False
    assert manager.get_file_entry("docs/report.txt")["mtime"] == 200.0


def test_cleanup_missing_files_removes_only_absent_entries(tmp_path):
    manager = UploadIndexManager(index_file=str(tmp_path / "index.json"), autosave_interval=0)
    manager.add_file_entry("keep.txt", 1, 1.0, "a", "id-1")
    manager.add_file_entry("drop.txt", 1, 1.0, "b", "id-2")

    removed = manager.cleanup_missing_files({"keep.txt"})

    assert removed == 1
    assert manager.get_file_entry("keep.txt") is not None
    assert manager.get_file_entry("drop.txt") is None
