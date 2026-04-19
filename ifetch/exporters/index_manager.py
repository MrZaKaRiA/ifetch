"""
Index Manager for Google Drive Export.

This module manages an index of uploaded files to enable incremental uploads
and avoid re-scanning/re-uploading unchanged files.
"""

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
import hashlib

# Try to use orjson for much faster JSON serialization (5-10x faster)
try:
    import orjson
    HAS_ORJSON = True
except ImportError:
    HAS_ORJSON = False


class UploadIndexManager:
    """Manages an index of uploaded files for incremental sync."""

    def __init__(
        self,
        index_file: str = '.gdrive_upload_index.json',
        autosave_interval: int = 100
    ):
        """
        Initialize the upload index manager.

        Args:
            index_file: Path to the index file (JSON format)
            autosave_interval: Automatically save every N file additions (0 = disabled)
        """
        self.index_file = Path(index_file)
        self.index: Dict[str, Dict[str, Any]] = {}
        self.autosave_interval = autosave_interval
        self._pending_saves = 0  # Track unsaved changes
        self._load_index()

    def _load_index(self) -> None:
        """Load the index from disk."""
        if self.index_file.exists():
            try:
                if HAS_ORJSON:
                    # Use orjson for faster loading
                    with open(self.index_file, 'rb') as f:
                        self.index = orjson.loads(f.read())
                else:
                    # Fallback to standard json
                    with open(self.index_file, 'r') as f:
                        self.index = json.load(f)
                print(f"Loaded upload index: {len(self.index)} files tracked")
                if HAS_ORJSON:
                    print("  Using orjson for fast JSON operations")
            except Exception as e:
                print(f"Warning: Could not load index file: {e}")
                self.index = {}
        else:
            print("No existing upload index found. Starting fresh.")
            self.index = {}

    def save_index(self, force: bool = False) -> None:
        """
        Save the index to disk.

        Args:
            force: Force save even if autosave interval not reached
        """
        # Skip save if no pending changes and not forced
        if not force and self._pending_saves == 0:
            return

        try:
            # Create backup if index exists
            if self.index_file.exists():
                backup_file = self.index_file.with_suffix('.json.backup')
                self.index_file.rename(backup_file)

            if HAS_ORJSON:
                # Use orjson for much faster serialization (5-10x faster)
                # orjson.dumps returns bytes, so write in binary mode
                with open(self.index_file, 'wb') as f:
                    # OPT_INDENT_2 for pretty printing (optional, remove for faster saves)
                    f.write(orjson.dumps(self.index, option=orjson.OPT_INDENT_2))
            else:
                # Fallback to standard json (slower)
                with open(self.index_file, 'w') as f:
                    json.dump(self.index, f, indent=2)

            # Remove backup if save was successful
            backup_file = self.index_file.with_suffix('.json.backup')
            if backup_file.exists():
                backup_file.unlink()

            # Reset pending saves counter
            self._pending_saves = 0

        except Exception as e:
            print(f"Error saving index file: {e}")
            # Restore backup if save failed
            backup_file = self.index_file.with_suffix('.json.backup')
            if backup_file.exists():
                backup_file.rename(self.index_file)

    def get_file_entry(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Get index entry for a file.

        Args:
            file_path: Relative path to the file

        Returns:
            File entry dict or None if not found
        """
        return self.index.get(file_path)

    def add_file_entry(
        self,
        file_path: str,
        file_size: int,
        mtime: float,
        checksum: str,
        gdrive_file_id: str
    ) -> None:
        """
        Add or update a file entry in the index.

        Args:
            file_path: Relative path to the file
            file_size: File size in bytes
            mtime: Last modified timestamp
            checksum: MD5 checksum
            gdrive_file_id: Google Drive file ID
        """
        self.index[file_path] = {
            'size': file_size,
            'mtime': mtime,
            'checksum': checksum,
            'gdrive_file_id': gdrive_file_id,
            'upload_time': datetime.now().isoformat(),
            'upload_timestamp': datetime.now().timestamp()
        }

        # Track pending saves
        self._pending_saves += 1

        # Auto-save if interval reached
        if self.autosave_interval > 0 and self._pending_saves >= self.autosave_interval:
            print(f"Auto-saving index ({self._pending_saves} pending changes)...")
            self.save_index()

    def remove_file_entry(self, file_path: str) -> None:
        """
        Remove a file entry from the index.

        Args:
            file_path: Relative path to the file
        """
        if file_path in self.index:
            del self.index[file_path]

    def file_needs_upload(
        self,
        file_path: str,
        current_size: int,
        current_mtime: float,
        current_checksum: Optional[str] = None
    ) -> bool:
        """
        Check if a file needs to be uploaded.

        Args:
            file_path: Relative path to the file
            current_size: Current file size
            current_mtime: Current modification time
            current_checksum: Current MD5 checksum (optional, calculated if needed)

        Returns:
            True if file needs upload, False if it's up to date
        """
        entry = self.get_file_entry(file_path)

        if entry is None:
            # File not in index, needs upload
            return True

        # Quick checks: size or mtime changed
        if entry['size'] != current_size:
            return True

        if entry['mtime'] != current_mtime:
            # mtime changed, but might be false positive
            # Check checksum to be sure
            if current_checksum is None:
                # If no checksum provided, assume changed
                return True
            if entry['checksum'] != current_checksum:
                return True
            else:
                # Checksum matches despite mtime change (e.g., file touched)
                # Update mtime in index
                entry['mtime'] = current_mtime
                return False

        # Size and mtime are the same
        # If checksum is provided, verify it matches
        if current_checksum is not None:
            if entry['checksum'] != current_checksum:
                # Checksum changed (rare but possible)
                return True

        # File hasn't changed
        return False

    def get_gdrive_file_id(self, file_path: str) -> Optional[str]:
        """
        Get the Google Drive file ID for a file.

        Args:
            file_path: Relative path to the file

        Returns:
            Google Drive file ID or None
        """
        entry = self.get_file_entry(file_path)
        return entry['gdrive_file_id'] if entry else None

    def clear_index(self) -> None:
        """Clear the entire index."""
        self.index = {}
        print("Index cleared")

    def rebuild_index(self) -> None:
        """
        Rebuild the index by clearing it.
        Note: Actual rebuilding happens during next upload scan.
        """
        self.clear_index()
        self.save_index()
        print("Index rebuilt (cleared). Will be repopulated on next upload.")

    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the index.

        Returns:
            Dictionary with index statistics
        """
        if not self.index:
            return {
                'total_files': 0,
                'total_size': 0,
                'oldest_upload': None,
                'newest_upload': None
            }

        total_size = sum(entry['size'] for entry in self.index.values())
        upload_times = [
            entry.get('upload_timestamp', 0)
            for entry in self.index.values()
            if entry.get('upload_timestamp')
        ]

        return {
            'total_files': len(self.index),
            'total_size': total_size,
            'total_size_gb': total_size / (1024**3),
            'oldest_upload': datetime.fromtimestamp(min(upload_times)).isoformat() if upload_times else None,
            'newest_upload': datetime.fromtimestamp(max(upload_times)).isoformat() if upload_times else None
        }

    def print_stats(self) -> None:
        """Print index statistics."""
        stats = self.get_stats()

        print(f"\n{'='*60}")
        print("UPLOAD INDEX STATISTICS")
        print(f"{'='*60}\n")

        print(f"Total files tracked: {stats['total_files']}")
        print(f"Total size: {stats['total_size_gb']:.2f} GB")

        if stats['oldest_upload']:
            print(f"Oldest upload: {stats['oldest_upload']}")
        if stats['newest_upload']:
            print(f"Newest upload: {stats['newest_upload']}")

        print(f"\n{'='*60}\n")

    def cleanup_missing_files(self, existing_paths: set) -> int:
        """
        Remove entries for files that no longer exist locally.

        Args:
            existing_paths: Set of file paths that still exist

        Returns:
            Number of entries removed
        """
        to_remove = []
        for file_path in self.index.keys():
            if file_path not in existing_paths:
                to_remove.append(file_path)

        for file_path in to_remove:
            self.remove_file_entry(file_path)

        if to_remove:
            print(f"Cleaned up {len(to_remove)} entries for deleted files")

        return len(to_remove)

    @staticmethod
    def calculate_checksum(file_path: Path) -> str:
        """
        Calculate MD5 checksum for a file.

        Args:
            file_path: Path to the file

        Returns:
            MD5 checksum as hex string
        """
        hash_md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
