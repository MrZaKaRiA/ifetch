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


# ---------------------------------------------------------------------------
# Tests for shared-file download fallback and fast-fail behaviour
# ---------------------------------------------------------------------------

class _WSONotFoundError(Exception):
    """Simulates pyicloud WSObjectNotFound 404 response."""
    def __str__(self):
        return "Not Found (404): WSObjectNotFound ObjectNotFoundException: Could not find document"


class _SharedFileItem:
    """Fake DriveNode whose open() always raises WSObjectNotFound (shared-file scenario)."""
    name = "shared_photo.jxl"
    data = {
        "docwsid": "owner-doc-uuid",
        "drivewsid": "FILE::com.apple.CloudDocs::owner-drive-uuid",
        "zone": "com.apple.CloudDocs",
        "size": 1024,
    }

    def open(self, **kwargs):
        raise _WSONotFoundError()


import unittest.mock as mock
import pytest


class _FakeDriveWithGetFile:
    """Fake DriveService that records get_file calls and always raises."""
    def __init__(self):
        self.get_file_calls = []
        self.get_node_data_calls = []

    def get_file(self, file_id, zone=None, **kwargs):
        self.get_file_calls.append((file_id, zone))
        raise _WSONotFoundError()

    def get_node_data(self, drivewsid):
        self.get_node_data_calls.append(drivewsid)
        return {}  # no downloadURL key


class _FakeDriveWithShareID:
    """Fake DriveService simulating pyicloud v2.5 (timlaing fork) with shareID support.

    Strategy 1 in _try_shared_open constructs the download/by_id request manually
    via drive.session, including the shareID param.  This fake captures that call.
    """
    params = {"clientId": "fake-client"}
    _document_root = "https://idmsa.apple.com/drive"

    def __init__(self, success_response):
        self._success_response = success_response
        self.session = mock.MagicMock()
        # Simulate a successful 200 response with a data_token URL
        ok_resp = mock.MagicMock()
        ok_resp.ok = True
        ok_resp.json.return_value = {
            "data_token": {"url": "https://cdn.icloud.com/shared/file.jxl"}
        }
        self.session.get.return_value = ok_resp

    def get_file(self, file_id, zone=None, **kwargs):
        raise _WSONotFoundError()

    def get_node_data(self, drivewsid):
        return {}


class _SharedFileItemWithShareID:
    """DriveNode for a file shared from another user, data includes shareID."""
    name = "shared_photo.jxl"
    data = {
        "docwsid": "owner-doc-uuid",
        "drivewsid": "FILE::com.apple.CloudDocs::owner-drive-uuid",
        "zone": "com.apple.CloudDocs",
        "size": 1024,
        "shareID": {"shareRecordName": "share-record-abc123"},
    }

    def open(self, **kwargs):
        raise _WSONotFoundError()


class _FakeDriveWithDirectURL:
    """Fake DriveService whose get_node_data returns a direct download URL."""
    def __init__(self):
        pass

    def get_file(self, file_id, zone=None, **kwargs):
        raise _WSONotFoundError()

    def get_node_data(self, drivewsid):
        return {"downloadURL": "https://cdn.icloud.com/fake/file"}


def test_open_with_retry_fast_fails_on_wsobjectnotfound(monkeypatch):
    """WSObjectNotFound should not trigger repeated get_drive_item refreshes."""
    monkeypatch.setattr("ifetch.downloader.PyiCloudService", _FakePyiCloudService)

    dm = DownloadManager(email="me@example.com", max_retries=3)
    dm.authenticate()

    fake_drive = _FakeDriveWithGetFile()
    dm.api.drive = fake_drive

    item = _SharedFileItem()
    with pytest.raises(_WSONotFoundError):
        dm._open_with_retry(item)

    # Should have tried drivewsid fallback (strategy 2) exactly once, NOT retried 3×
    assert len(fake_drive.get_file_calls) == 1
    assert fake_drive.get_file_calls[0][0] == item.data["drivewsid"]


def test_try_shared_open_sends_share_id_in_download_request(monkeypatch):
    """Strategy 1: shareID is included in the /download/by_id request params."""
    monkeypatch.setattr("ifetch.downloader.PyiCloudService", _FakePyiCloudService)

    dm = DownloadManager(email="me@example.com")
    dm.authenticate()

    content_resp = mock.MagicMock()
    content_resp.ok = True
    fake_drive = _FakeDriveWithShareID(success_response=content_resp)
    # Make the second session.get (CDN fetch) return our content response
    fake_drive.session.get.side_effect = [
        fake_drive.session.get.return_value,  # first call: download/by_id token fetch
        content_resp,                          # second call: CDN content fetch
    ]
    dm.api.drive = fake_drive

    item = _SharedFileItemWithShareID()
    result = dm._try_shared_open(item)

    assert result is content_resp

    # First call must have included shareID and document_id in params
    first_call_params = fake_drive.session.get.call_args_list[0]
    actual_params = first_call_params[1].get("params") or first_call_params[0][1]
    assert actual_params.get("document_id") == "owner-doc-uuid"
    assert actual_params.get("shareID") == {"shareRecordName": "share-record-abc123"}


def test_try_shared_open_uses_node_data_url(monkeypatch):
    """Strategy 3: _try_shared_open succeeds when get_node_data returns a direct URL."""
    monkeypatch.setattr("ifetch.downloader.PyiCloudService", _FakePyiCloudService)

    dm = DownloadManager(email="me@example.com")
    dm.authenticate()

    fake_response = mock.MagicMock()
    dm.api.drive = _FakeDriveWithDirectURL()
    dm.http.get = mock.MagicMock(return_value=fake_response)

    item = _SharedFileItem()
    result = dm._try_shared_open(item)

    assert result is fake_response
    dm.http.get.assert_called_once_with(
        "https://cdn.icloud.com/fake/file", stream=True, timeout=30
    )
