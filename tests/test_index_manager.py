import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from ifetch.exporters.index_manager import UploadIndexManager  # noqa: E402


def test_save_and_reload_index(tmp_path):
    index_file = tmp_path / "index.json"
    manager = UploadIndexManager(index_file=str(index_file), autosave_interval=0)
    manager.add_file_entry("docs/report.txt", 10, 100.0, "abc", "file-1")

    manager.save_index(force=True)

    reloaded = UploadIndexManager(index_file=str(index_file), autosave_interval=0)
    assert reloaded.get_file_entry("docs/report.txt")["gdrive_file_id"] == "file-1"


def test_add_entry_autosaves_when_threshold_reached(tmp_path, monkeypatch):
    manager = UploadIndexManager(index_file=str(tmp_path / "index.json"), autosave_interval=1)
    calls = []

    monkeypatch.setattr(manager, "save_index", lambda force=False: calls.append(force))

    manager.add_file_entry("docs/report.txt", 10, 100.0, "abc", "file-1")

    assert calls == [False]


def test_save_index_restores_backup_on_failure(tmp_path, monkeypatch):
    index_file = tmp_path / "index.json"
    index_file.write_text('{"existing": {"size": 1}}')
    manager = UploadIndexManager(index_file=str(index_file), autosave_interval=0)
    manager.index = {"new": {"size": 2}}
    manager._pending_saves = 1

    original_open = open

    def _failing_open(path, mode="r", *args, **kwargs):
        if Path(path) == index_file and "w" in mode:
            raise OSError("disk full")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _failing_open)

    manager.save_index(force=True)

    assert index_file.exists()
    assert '"existing"' in index_file.read_text()


def test_remove_file_entry_and_get_gdrive_id(tmp_path):
    manager = UploadIndexManager(index_file=str(tmp_path / "index.json"), autosave_interval=0)
    manager.add_file_entry("docs/report.txt", 10, 100.0, "abc", "file-1")

    assert manager.get_gdrive_file_id("docs/report.txt") == "file-1"
    manager.remove_file_entry("docs/report.txt")
    manager.remove_file_entry("docs/missing.txt")

    assert manager.get_file_entry("docs/report.txt") is None
    assert manager.get_gdrive_file_id("docs/report.txt") is None


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


def test_file_needs_upload_branches(tmp_path):
    manager = UploadIndexManager(index_file=str(tmp_path / "index.json"), autosave_interval=0)
    manager.add_file_entry("docs/report.txt", 10, 100.0, "abc", "file-1")

    assert manager.file_needs_upload("missing.txt", 1, 1.0) is True
    assert manager.file_needs_upload("docs/report.txt", 11, 100.0) is True
    assert manager.file_needs_upload("docs/report.txt", 10, 101.0) is True
    assert manager.file_needs_upload("docs/report.txt", 10, 100.0, current_checksum="different") is True
    assert manager.file_needs_upload("docs/report.txt", 10, 100.0, current_checksum="abc") is False


def test_get_stats_print_stats_clear_and_rebuild(tmp_path, capsys):
    manager = UploadIndexManager(index_file=str(tmp_path / "index.json"), autosave_interval=0)
    empty_stats = manager.get_stats()
    assert empty_stats["total_files"] == 0
    assert empty_stats["oldest_upload"] is None

    manager.add_file_entry("docs/a.txt", 10, 100.0, "abc", "file-1")
    manager.add_file_entry("docs/b.txt", 20, 200.0, "def", "file-2")
    stats = manager.get_stats()

    assert stats["total_files"] == 2
    assert stats["total_size"] == 30
    assert stats["oldest_upload"] is not None
    assert stats["newest_upload"] is not None

    manager.print_stats()
    output = capsys.readouterr().out
    assert "UPLOAD INDEX STATISTICS" in output

    manager.clear_index()
    assert manager.index == {}

    manager.add_file_entry("docs/c.txt", 5, 300.0, "ghi", "file-3")
    manager.rebuild_index()
    output = capsys.readouterr().out
    assert "Index rebuilt" in output
    assert manager.index == {}


def test_calculate_checksum(tmp_path):
    file_path = tmp_path / "file.bin"
    file_path.write_bytes(b"hello")

    checksum = UploadIndexManager.calculate_checksum(file_path)

    assert checksum == "5d41402abc4b2a76b9719d911017c592"
