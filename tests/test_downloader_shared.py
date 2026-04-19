import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from ifetch.downloader import DownloadManager  # noqa: E402


class _FakeSharedItem(dict):
    """Behaves like iCloud drive folder for shared items."""
    def __getitem__(self, key):
        return super().__getitem__(key)
    def dir(self):
        return self

class _FakeDrive(dict):
    """Root drive returning owned and shared collections."""
    def __init__(self):
        super().__init__()
        self.shared = _FakeSharedItem()

    def __getitem__(self, key):
        return super().__getitem__(key)

    def dir(self):
        return self


class _FakePyiCloudService:
    def __init__(self, **kwargs):
        self.drive = _FakeDrive()
        # Populate
        # Owned file structure: "Docs/Personal.txt"
        self.drive["Docs"] = _FakeSharedItem()
        self.drive["Docs"]["Personal.txt"] = object()
        # Shared structure: "SharedRoot/Sub/File.txt"
        shared_root = _FakeSharedItem()
        shared_root["Sub"] = _FakeSharedItem()
        shared_root["Sub"]["File.txt"] = object()
        self.drive.shared["SharedRoot"] = shared_root
    requires_2fa = False
    requires_2sa = False


def test_get_drive_item_shared(monkeypatch):
    monkeypatch.setattr("ifetch.downloader.PyiCloudService", _FakePyiCloudService)

    dm = DownloadManager(email="me@example.com")
    dm.authenticate()  # will use fake service

    # Path within shared area
    item = dm.get_drive_item("SharedRoot/Sub/File.txt")
    assert item is not None

    # Path within owned area still works
    owned = dm.get_drive_item("Docs/Personal.txt")
    assert owned is not None


class _LazyRoot:
    """Simulate a root that only exposes children through dir()."""
    def __init__(self):
        self.shared = None
        self._listed = False
        self._children = {"Documents": _FakeSharedItem()}
        self._children["Documents"]["Notes.txt"] = object()

    def __getitem__(self, key):
        if not self._listed:
            raise KeyError(key)
        return self._children[key]

    def dir(self):
        self._listed = True
        return list(self._children.keys())


class _LazyPyiCloudService:
    requires_2fa = False
    requires_2sa = False

    def __init__(self, **kwargs):
        self.drive = _LazyRoot()


def test_get_drive_item_resolves_from_directory_listing(monkeypatch):
    monkeypatch.setattr("ifetch.downloader.PyiCloudService", _LazyPyiCloudService)

    dm = DownloadManager(email="me@example.com")
    dm.authenticate()

    item = dm.get_drive_item("Documents")
    assert item is not None


def test_get_drive_item_case_insensitive(monkeypatch):
    monkeypatch.setattr("ifetch.downloader.PyiCloudService", _FakePyiCloudService)

    dm = DownloadManager(email="me@example.com")
    dm.authenticate()

    item = dm.get_drive_item("docs/personal.txt")
    assert item is not None
