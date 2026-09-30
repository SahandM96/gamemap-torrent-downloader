#!/usr/bin/env python3
"""Torrent Desk — state + preferences on disk (plain JSON, no pip deps).

Keeps the download registry so the UI can show name/magnet/destination for items
aria2c has already forgotten (e.g. after a daemon restart), which is what makes
"resume the half-downloaded file" possible.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_RECENT_DIRS = 15

# status values owned by us (aria2 statuses are separate: active/waiting/...)
ST_QUEUED = "queued"
ST_PAUSED = "paused"
ST_DONE = "done"
ST_ERROR = "error"
ST_REMOVED = "removed"
ST_MISSING = "missing"  # registry row, but aria2 no longer knows the gid

DEFAULT_PREFS: Dict[str, Any] = {
    "default_dir": "",
    "proxy": "",
    "max_concurrent": 3,
    "file_allocation": "none",
    "start_paused": False,
    "create_dir": True,
    "sources_disabled": [],
}


def _empty() -> Dict[str, Any]:
    return {"downloads": [], "recent_dirs": [], "source_dirs": {}, "prefs": {}}


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._read()
        self._data.setdefault("downloads", [])
        self._data.setdefault("recent_dirs", [])
        self._data.setdefault("source_dirs", {})
        self._data.setdefault("prefs", {})

    # ------------------------------------------------------------------ io
    def _read(self) -> Dict[str, Any]:
        if not self.path.is_file():
            return _empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return _empty()
        return data if isinstance(data, dict) else _empty()

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # ------------------------------------------------------------- prefs
    def prefs(self) -> Dict[str, Any]:
        merged = dict(DEFAULT_PREFS)
        merged.update({k: v for k, v in (self._data.get("prefs") or {}).items() if v is not None})
        return merged

    def set_prefs(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        prefs = self._data.setdefault("prefs", {})
        for key, value in patch.items():
            if key in DEFAULT_PREFS:
                prefs[key] = value
        self.save()
        return self.prefs()

    def recent_dirs(self) -> List[str]:
        return list(self._data.get("recent_dirs") or [])

    def note_dir(self, directory: str) -> None:
        directory = str(directory)
        if not directory:
            return
        dirs = [d for d in self._data.get("recent_dirs") or [] if d != directory]
        dirs.insert(0, directory)
        self._data["recent_dirs"] = dirs[:MAX_RECENT_DIRS]
        self.save()

    def source_dir(self, source: str) -> str:
        return str((self._data.get("source_dirs") or {}).get(source) or "")

    def set_source_dir(self, source: str, directory: str) -> None:
        mapping = self._data.setdefault("source_dirs", {})
        if directory:
            mapping[source] = str(directory)
        else:
            mapping.pop(source, None)
        self.save()

    # --------------------------------------------------------- downloads
    def downloads(self) -> List[Dict[str, Any]]:
        return list(self._data.get("downloads") or [])

    def get(self, download_id: str) -> Optional[Dict[str, Any]]:
        for row in self._data.get("downloads") or []:
            if row.get("id") == download_id:
                return row
        return None

    def find_by_gid(self, gid: str) -> Optional[Dict[str, Any]]:
        for row in self._data.get("downloads") or []:
            if row.get("gid") == gid:
                return row
        return None

    def add(self, **fields: Any) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "gid": None,
            "name": "",
            "source": "",
            "source_label": "",
            "magnet": None,
            "torrent_url": None,
            "page_url": None,
            "info_hash": None,
            "torrent_sha1": None,
            "dir": "",
            "size_bytes": None,
            "status": ST_QUEUED,
            "error": None,
            "files": [],
            "added_at": int(time.time()),
            "finished_at": None,
        }
        row.update(fields)
        self._data.setdefault("downloads", []).insert(0, row)
        self.save()
        return row

    def update(self, download_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        row = self.get(download_id)
        if row is None:
            return None
        row.update(fields)
        self.save()
        return row

    def remove(self, download_id: str) -> Optional[Dict[str, Any]]:
        rows = self._data.get("downloads") or []
        found = None
        for index, row in enumerate(rows):
            if row.get("id") == download_id:
                found = rows.pop(index)
                break
        if found is not None:
            self.save()
        return found
