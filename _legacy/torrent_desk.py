#!/usr/bin/env python3
"""GameMap Torrent Desk — local torrent search + aria2c download manager.

Stdlib only (like tools/gamemap-mail-desk). Serves a small localhost UI that
searches the 10 Kickass-Torrents alternatives from the bitbrowser roundup,
hands magnet/.torrent picks to aria2c, asks the destination folder per item and
lets you manage both the queue and the half-downloaded files on disk.
"""
from __future__ import annotations

import json
import os
import secrets
import shlex
import shutil
import subprocess
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

import aria as aria_mod
import sources as sources_mod
from net import (
    format_size,
    magnet_first,
    magnet_infohash,
    magnet_name,
    normalize_magnet,
    parse_torrent_bytes,
)
from store import ST_DONE, ST_ERROR, ST_MISSING, ST_PAUSED, ST_QUEUED, ST_REMOVED, Store

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
RUN_DIR = BASE_DIR / "run"


def _load_dotenv() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

HOST = os.getenv("TORRENT_DESK_HOST", "127.0.0.1")
PORT = int(os.getenv("TORRENT_DESK_PORT", "8811"))
ARIA_PORT = int(os.getenv("TORRENT_DESK_ARIA_PORT", "6811"))
ARIA_BIN = os.getenv("TORRENT_DESK_ARIA_BIN", "aria2c")
DEFAULT_DIR = os.path.expanduser(os.getenv("TORRENT_DESK_DEFAULT_DIR") or "~/Downloads/Torrents")
SEARCH_TIMEOUT = float(os.getenv("TORRENT_DESK_TIMEOUT", "14"))
DETAIL_LIMIT = int(os.getenv("TORRENT_DESK_DETAIL_LIMIT", "6"))
EZTV_PAGES = int(os.getenv("TORRENT_DESK_EZTV_PAGES", "4"))
ARIA_EXTRA = shlex.split(os.getenv("TORRENT_DESK_ARIA_EXTRA", ""))

STATE_FILE = RUN_DIR / "state.json"
SESSION_FILE = RUN_DIR / "aria2.session"
LOG_FILE = RUN_DIR / "aria2.log"
SECRET_FILE = RUN_DIR / "rpc.secret"


def _rpc_secret() -> str:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    if SECRET_FILE.is_file():
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(18)
    SECRET_FILE.write_text(token, encoding="utf-8")
    return token


STORE = Store(STATE_FILE)


def prefs() -> Dict[str, Any]:
    data = STORE.prefs()
    data["default_dir"] = data.get("default_dir") or DEFAULT_DIR
    data["proxy"] = data.get("proxy") or os.getenv("TORRENT_DESK_PROXY", "")
    return data


def ctx() -> sources_mod.Ctx:
    current = prefs()
    return sources_mod.Ctx(
        proxy=str(current.get("proxy") or ""),
        timeout=SEARCH_TIMEOUT,
        detail_limit=DETAIL_LIMIT,
        eztv_pages=EZTV_PAGES,
    )


def aria() -> aria_mod.Aria2:
    current = prefs()
    return aria_mod.Aria2(
        binary=ARIA_BIN,
        port=ARIA_PORT,
        secret=_rpc_secret(),
        session_file=SESSION_FILE,
        log_file=LOG_FILE,
        default_dir=str(current.get("default_dir") or DEFAULT_DIR),
        max_concurrent=int(current.get("max_concurrent") or 3),
        file_allocation=str(current.get("file_allocation") or "none"),
        proxy=str(current.get("proxy") or ""),
        extra_args=ARIA_EXTRA,
    )



# ------------------------------------------------------------------ helpers
def resolve_dir(raw: str, create: bool = True) -> str:
    """Absolute, expanded destination folder. Creates it only when asked."""
    candidate = os.path.expanduser(str(raw or "").strip())
    if not candidate:
        raise ValueError("مقصد را وارد کنید")
    if not candidate.startswith("/"):
        raise ValueError("مسیر مقصد باید مطلق باشد (مثلاً /home/user/Downloads)")
    path = Path(candidate)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    elif not path.is_dir():
        raise ValueError(f"پوشه وجود ندارد: {path}")
    if not os.access(path, os.W_OK):
        raise ValueError(f"اجازه نوشتن در {path} نیست")
    return str(path)


def dest_for(source: str, requested: str, remember: bool, create: bool) -> str:
    wanted = requested or STORE.source_dir(source) or str(prefs().get("default_dir") or DEFAULT_DIR)
    directory = resolve_dir(wanted, create=create)
    STORE.note_dir(directory)
    if remember:
        STORE.set_source_dir(source, directory)
    return directory


def open_folder(path: str) -> str:
    target = Path(os.path.expanduser(path))
    if not target.exists():
        raise ValueError(f"مسیر وجود ندارد: {target}")
    subprocess.Popen(
        ["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return str(target)


def _torrent_bytes(url: str) -> bytes:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = RUN_DIR / f"fetch-{int(time.time())}.torrent"
    proc = subprocess.run(
        ["curl", "-sS", "-L", "--max-time", str(int(SEARCH_TIMEOUT)), "-o", str(tmp), url],
        capture_output=True, check=False,
    )
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < 32:
        tmp.unlink(missing_ok=True)
        raise ValueError("دریافت فایل .torrent نشد (شاید پروکسی لازم دارد)")
    data = tmp.read_bytes()
    tmp.unlink(missing_ok=True)
    return data


def browse_dirs(raw: str) -> Dict[str, Any]:
    """Tiny folder browser so a destination can be picked without native dialogs."""
    path = Path(os.path.expanduser(str(raw or "~"))).resolve()
    while not path.is_dir() and path != path.parent:
        path = path.parent
    entries: List[Dict[str, str]] = []
    try:
        children = sorted(path.iterdir(), key=lambda p: (p.name.startswith("."), p.name.lower()))
    except PermissionError as exc:
        raise ValueError(f"اجازه خواندن {path} نیست") from exc
    for child in children:
        if len(entries) >= 500:
            break
        try:
            if child.is_dir() and not child.is_symlink():
                entries.append({"name": child.name, "path": str(child)})
        except OSError:
            continue
    return {
        "path": str(path),
        "parent": str(path.parent) if path.parent != path else "",
        "writable": os.access(path, os.W_OK),
        "dirs": entries,
    }


# --------------------------------------------------------------- downloads
LIVE_STATUS = {
    "active": "downloading", "waiting": "queued", "paused": "paused",
    "complete": "done", "error": "error", "removed": "removed",
}


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def downloads_view() -> Dict[str, Any]:
    handle = aria()
    up = handle.is_up()
    live = handle.all_status() if up else {}
    items: List[Dict[str, Any]] = []
    for row in STORE.downloads():
        gid = str(row.get("gid") or "")
        status_row = live.get(gid) or {}
        total = _int(status_row.get("totalLength"))
        done = _int(status_row.get("completedLength"))
        speed = _int(status_row.get("downloadSpeed"))
        status = LIVE_STATUS.get(str(status_row.get("status")), "")
        if not status_row:
            status = str(row.get("status") or ST_QUEUED)
            if status in (ST_QUEUED, ST_PAUSED) and gid:
                status = ST_MISSING  # aria2 forgot it (restart / removed)
        # Snapshot file paths while aria2 still knows them, so stop/delete/move
        # can find the partials later (after a stop the gid is gone forever).
        # Refresh too when stored paths disagree with aria2's dir (resume after
        # a move re-adds the item → new paths in the new dir).
        existing_files = row.get("files") or []
        status_dir = str((status_row or {}).get("dir") or "")
        row_dir = str(row.get("dir") or "")
        stored_ok = bool(
            row_dir and existing_files and all(
                str(entry.get("path", "") if isinstance(entry, dict) else entry).startswith(
                    row_dir.rstrip("/") + "/"
                )
                for entry in existing_files
            )
        )
        stale = bool(
            status_row and status_dir and existing_files and any(
                not str(
                    entry.get("path", "") if isinstance(entry, dict) else entry
                ).startswith(status_dir.rstrip("/") + "/")
                for entry in existing_files
            )
        )
        # Don't let a lagging aria2 row (still reporting the pre-move dir right
        # after a move+resume) clobber stored paths that already point at the
        # row's dir — that would resurrect stale paths and break delete.
        if status_row and (not existing_files or (stale and not stored_ok)):
            snapshot = aria_mod.describe_files(status_row)
            info_hash = str(status_row.get("infoHash") or "") or None
            update_kw: Dict[str, Any] = {}
            if snapshot:
                update_kw["files"] = snapshot
            if info_hash:
                update_kw["info_hash"] = info_hash
            if update_kw:
                STORE.update(row["id"], **update_kw)
                row = STORE.get(row["id"]) or row
        if status == "done" and row.get("status") != ST_DONE:
            files = aria_mod.describe_files(status_row) if status_row else (row.get("files") or [])
            STORE.update(row["id"], status=ST_DONE, finished_at=int(time.time()), files=files)
            row = STORE.get(row["id"]) or row
        progress = round(done * 100 / total, 1) if total else (100.0 if row.get("status") == ST_DONE else 0.0)
        eta = int((total - done) / speed) if speed and total > done else None
        files = (aria_mod.describe_files(status_row) if status_row else (row.get("files") or []))[:200]
        items.append(
            {
                "id": row.get("id"), "gid": gid,
                "name": row.get("name") or magnet_name(str(row.get("magnet") or "")) or "(بدون نام)",
                "source": row.get("source") or "manual",
                "source_label": row.get("source_label") or row.get("source") or "دستی",
                "dir": status_row.get("dir") or row.get("dir"),
                "status": status, "progress": progress,
                "total_bytes": total or _int(row.get("size_bytes")),
                "done_bytes": done,
                "total_human": format_size(total or _int(row.get("size_bytes"))),
                "done_human": format_size(done),
                "speed": speed, "speed_human": f"{format_size(speed)}/s" if speed else "",
                "eta_seconds": eta,
                "seeds": _int(status_row.get("numSeeders")) if status_row else None,
                "connections": _int(status_row.get("connections")) if status_row else None,
                "error": str(status_row.get("errorMessage") or row.get("error") or ""),
                "magnet": row.get("magnet"), "torrent_url": row.get("torrent_url"),
                "page_url": row.get("page_url"), "added_at": row.get("added_at"),
                "finished_at": row.get("finished_at"), "files": files,
                "resumable": bool(row.get("magnet") or row.get("torrent_url")),
                "info_hash": row.get("info_hash") or (str(status_row.get("infoHash") or "") if status_row else None),

            }
        )
    items.sort(key=lambda item: -(item.get("added_at") or 0))
    return {
        "aria2": {
            "up": up,
            "version": handle.version() if up else "",
            "endpoint": handle.endpoint,
            "global": handle.global_stat() if up else {},
        },
        "items": items,
    }


def _readd(handle: aria_mod.Aria2, row: Dict[str, Any], directory: str, paused: bool = False) -> str:
    """Hand an item back to aria2 in the same folder → it resumes from the .aria2 file."""
    magnet = str(row.get("magnet") or "")
    torrent_url = str(row.get("torrent_url") or "")
    if magnet:
        return handle.add_magnet(magnet, directory, paused=paused)
    if torrent_url:
        return handle.add_torrent(_torrent_bytes(torrent_url), directory, paused=paused)
    raise ValueError("مغناطیس ذخیره نشده؛ ادامه‌دادن ممکن نیست (از جست‌وجو دوباره اضافه کنید)")


def start_download(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    source = str(payload.get("source") or "manual").strip()
    magnet = normalize_magnet(str(payload.get("magnet") or ""))
    torrent_url = str(payload.get("torrent_url") or "").strip()
    if not magnet and not torrent_url:
        raise ValueError("مغناطیس یا فایل .torrent لازم است")
    directory = dest_for(
        source, str(payload.get("dir") or ""), bool(payload.get("remember")),
        bool(payload.get("create_dir", True)),
    )
    paused = bool(payload.get("paused"))
    handle = aria()
    handle.ensure_daemon()
    parsed_meta: Dict[str, Any] = {}
    if torrent_url:
        raw_torrent = _torrent_bytes(torrent_url)
        parsed_meta = parse_torrent_bytes(raw_torrent)
        gid = handle.add_torrent(raw_torrent, directory, paused=paused)
    else:
        gid = handle.add_magnet(magnet, directory, paused=paused)
    row = STORE.add(
        gid=gid, name=name or parsed_meta.get("name") or magnet_name(magnet) or torrent_url.rsplit("/", 1)[-1],
        source=source, source_label=str(payload.get("source_label") or source),
        magnet=magnet or None, torrent_url=torrent_url or None,
        page_url=str(payload.get("page_url") or "") or None,
        info_hash=parsed_meta.get("info_hash") or magnet_infohash(magnet) or None,
        torrent_sha1=parsed_meta.get("torrent_sha1"),
        dir=directory, size_bytes=_int(payload.get("size_bytes")) or None,
        status=ST_PAUSED if paused else ST_QUEUED,
    )
    return {"id": row["id"], "gid": gid, "dir": directory}


def control_download(download_id: str, action: str) -> Dict[str, Any]:
    row = STORE.get(download_id)
    if row is None:
        raise ValueError("چنین دانلودی در لیست نیست")
    handle = aria()
    gid = str(row.get("gid") or "")
    directory = str(row.get("dir") or prefs().get("default_dir") or DEFAULT_DIR)

    def snapshot_files() -> None:
        """Record file paths + infohash while aria2 still knows the gid."""
        if not gid or (row.get("files") and row.get("info_hash")):
            return
        try:
            live = handle.status(gid)
        except aria_mod.AriaError:
            return
        files = aria_mod.describe_files(live) or row.get("files") or []
        info_hash = str(live.get("infoHash") or "") or row.get("info_hash") or None
        if files or info_hash:
            STORE.update(download_id, files=files, info_hash=info_hash)
            row["files"] = files
            row["info_hash"] = info_hash

    if action == "pause":
        handle.ensure_daemon()
        if gid:
            handle.pause(gid)
            snapshot_files()
        STORE.update(download_id, status=ST_PAUSED)
    elif action == "resume":
        handle.ensure_daemon()
        if not (gid and gid in handle.all_status()):
            new_gid = _readd(handle, row, directory)
            STORE.update(download_id, gid=new_gid, status=ST_QUEUED, error=None)
        else:
            handle.unpause(gid)
            STORE.update(download_id, status=ST_QUEUED, error=None)
    elif action == "stop":
        handle.ensure_daemon()
        if gid:
            snapshot_files()
            handle.remove(gid)
        STORE.update(download_id, gid=None, status=ST_PAUSED)
    elif action == "forget":
        if gid:
            handle.purge_result(gid)
        STORE.remove(download_id)
    elif action == "reveal":
        open_folder(directory)
    else:
        raise ValueError(f"دستور ناشناخته: {action}")
    return {"id": download_id, "action": action}


def _item_paths(row: Dict[str, Any], directory: str, live_files: Any = None) -> List[Path]:
    """Everything aria2 put on disk for one item: data files, their control files,
    the directory-level `Name.aria2` and the saved `<infohash>.torrent`."""
    raw = []
    for entry in (live_files if live_files is not None else None) or (row.get("files") or []):
        path = str(entry.get("path") or "") if isinstance(entry, dict) else str(entry or "")
        if path:
            raw.append(Path(path))
    out: List[Path] = []
    for source_path in raw:
        out.append(source_path)
        out.append(Path(str(source_path) + ".aria2"))
    top = Path(directory) if directory else None
    if top and top.is_dir():
        infohash = (magnet_infohash(str(row.get("magnet") or ""))
                    or str(row.get("info_hash") or "")).lower()
        torrent_sha1 = str(row.get("torrent_sha1") or "").lower()
        parent_names = {p.parent.name for p in out if p.parent != top}
        file_names = {p.name for p in out}
        for control in top.glob("*.aria2"):
            if control.name[: -len(".aria2")] in parent_names | file_names:
                out.append(control)
        meta_hashes = {h for h in (infohash, torrent_sha1) if h}
        if meta_hashes:
            for meta in top.glob("*.torrent"):
                if meta.stem.lower() in meta_hashes:
                    out.append(meta)
    unique: List[Path] = []
    seen: set = set()
    for path in out:
        if str(path) not in seen:
            seen.add(str(path))
            unique.append(path)
    return unique

def _prune_empty_dirs(paths: List[str], base_dir: str) -> List[str]:
    """Recursively clean empty parent subdirectories of deleted/moved files."""
    cleaned: List[str] = []
    base_clean = base_dir.rstrip("/")
    folders = sorted({str(Path(p).parent) for p in paths}, key=len, reverse=True)
    for folder in folders:
        curr = Path(folder)
        while curr.is_dir() and str(curr) != base_clean and str(curr).startswith(base_clean + "/"):
            try:
                if not any(curr.iterdir()):
                    curr.rmdir()
                    cleaned.append(str(curr) + "/")
                    curr = curr.parent
                else:
                    break
            except OSError:
                break
    return cleaned




def move_destination(download_id: str, new_dir: str, create: bool = True) -> Dict[str, Any]:
    """Change the folder of a stopped/paused item: partial files + .aria2 move with it."""
    row = STORE.get(download_id)
    if row is None:
        raise ValueError("چنین دانلودی در لیست نیست")
    handle = aria()
    live = handle.all_status() if handle.is_up() else {}
    status_row = live.get(str(row.get("gid") or "")) or {}
    if str(status_row.get("status")) in ("active", "waiting"):
        raise ValueError("دانلود در جریان است؛ اول Pause یا «توقف» بزنید")
    target = resolve_dir(new_dir, create=create)
    old = str(row.get("dir") or "").rstrip("/")
    moved: List[str] = []
    if old and old != target:
        live = status_row.get("files") if status_row else None
        for candidate in _item_paths(row, old, live):
            if not candidate.is_absolute() or not str(candidate).startswith(old + "/"):
                continue
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(old)  # keeps the torrent's subfolders
            destination = Path(target) / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ValueError(f"در مقصد از قبل وجود دارد: {destination}")
            shutil.move(str(candidate), str(destination))
            moved.append(str(destination))
        _prune_empty_dirs([str(c) for c in _item_paths(row, old, live)], old)
    else:
        live = status_row.get("files") if status_row else None
    # Remap stored file paths to the target now: the resumed gid reports
    # target-dir paths via aria2, but a delete issued before the next poll
    # would otherwise try the stale old-dir paths and wipe nothing.
    remapped: List[Dict[str, Any]] = []
    for entry in row.get("files") or []:
        if isinstance(entry, dict):
            old_path = str(entry.get("path") or "")
            if old and old_path.startswith(old + "/"):
                entry = {**entry, "path": target + old_path[len(old):]}
            remapped.append(entry)
    STORE.update(download_id, dir=target, files=remapped or row.get("files") or [])
    if row.get("status") != ST_DONE and (row.get("magnet") or row.get("torrent_url")):
        gid = _readd(handle, row, target, paused=True)
        STORE.update(download_id, gid=gid, status=ST_PAUSED)
    STORE.note_dir(target)
    return {"id": download_id, "dir": target, "moved": moved}


def delete_download(download_id: str, remove_files: bool) -> Dict[str, Any]:
    """Drop an item from the list, optionally deleting its files/partials from disk."""
    row = STORE.get(download_id)
    if row is None:
        raise ValueError("چنین دانلودی در لیست نیست")
    handle = aria()
    gid = str(row.get("gid") or "")
    live = handle.all_status() if handle.is_up() else {}
    status_row = live.get(gid) or {}
    if str(status_row.get("status")) in ("active", "waiting") and gid:
        handle.remove(gid)
        time.sleep(0.6)
    deleted: List[str] = []
    if remove_files:
        directory = str(row.get("dir") or "").rstrip("/")
        live = status_row.get("files") if status_row else None
        candidates = _item_paths(row, directory, live)
        for candidate in candidates:
            if not candidate.is_absolute() or (directory and not str(candidate).startswith(directory + "/")):
                continue
            if candidate.is_file():
                candidate.unlink()
                deleted.append(str(candidate))
        # Live aria2 rows may drift after resume/re-add (name changed in the
        # new dir, or a poll overwrote stored paths): recursively delete the
        # item's root folders (.aria2 control + data were authoritative at
        # move time) so no half-download folder survives the delete.
        top = Path(directory) if directory else None
        if top and top.is_dir():
            roots: List[Path] = []
            for candidate in candidates:
                try:
                    rel = Path(str(candidate)).relative_to(directory)
                except ValueError:
                    continue
                root = top / rel.parts[0] if rel.parts else None
                if root is not None and root != top and str(root) not in {str(r) for r in roots}:
                    roots.append(root)
            for root in roots:
                if root.is_dir() and not root.is_symlink():
                    shutil.rmtree(root, ignore_errors=True)
                    deleted.append(str(root) + "/")
                elif root.is_file():
                    try:
                        root.unlink()
                        deleted.append(str(root))
                    except OSError:
                        pass
            # Left-over per-item control/metadata files sitting at the top.
            for extra in list(top.glob("*.aria2")) + list(top.glob("*.torrent")):
                if str(extra) not in deleted and extra.is_file():
                    try:
                        extra.unlink()
                        deleted.append(str(extra))
                    except OSError:
                        pass
        # multi-file torrents live in their own folder — prune it when now empty
        deleted.extend(_prune_empty_dirs(deleted, directory))
    if gid:
        handle.purge_result(gid)
    STORE.remove(download_id)
    return {"id": download_id, "deleted": deleted}


# ---------------------------------------------------------------- partials
def _walk_aria2(base: Path, depth: int) -> List[Path]:
    found: List[Path] = []
    base_depth = len(base.parts)
    for current, dirnames, filenames in os.walk(base):
        current_path = Path(current)
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if len(current_path.parts) - base_depth >= max(0, depth):
            dirnames[:] = []
        for filename in filenames:
            if filename.endswith(".aria2"):
                found.append(current_path / filename)
        if len(found) >= 400:
            break
    return found


def partials_view(root: str = "", depth: int = 3) -> Dict[str, Any]:
    """Find every *.aria2 control file under the chosen roots = half-downloaded items."""
    bases: List[str] = [root] if root else [
        str(prefs().get("default_dir") or DEFAULT_DIR), *STORE.recent_dirs()
    ]
    # drop nested roots (scanning ~/D/T plus ~/D/T/e2e would double-report)
    candidates = sorted({os.path.expanduser(str(b)) for b in bases if b}, key=len)
    roots: List[str] = []
    for candidate in candidates:
        if any(candidate == known or candidate.startswith(known.rstrip("/") + "/") for known in roots):
            continue
        if Path(candidate).is_dir():
            roots.append(candidate)
    rows: List[Dict[str, Any]] = []
    for base in roots:
        for control in _walk_aria2(Path(base), depth):
            target = Path(str(control)[: -len(".aria2")])
            owner: Optional[Dict[str, Any]] = None
            for row in STORE.downloads():
                if str(row.get("dir") or "").rstrip("/") == str(target.parent):
                    owner = row
                    break
            if target.is_file():
                size, exists = target.stat().st_size, True
            elif target.is_dir():
                # multi-file torrent: control file sits next to its folder
                size, exists = 0, True
                for sub in target.rglob("*"):
                    if sub.is_file() and not sub.name.endswith(".aria2"):
                        try:
                            size += sub.stat().st_size
                        except OSError:
                            continue
            else:
                size, exists = 0, False
            rows.append(
                {
                    "control": str(control), "target": str(target),
                    "name": target.name or control.name, "dir": str(target.parent),
                    "partial_bytes": size, "partial_human": format_size(size),
                    "is_dir": target.is_dir(), "exists": exists,
                    "download_id": (owner or {}).get("id"),
                    "download_name": (owner or {}).get("name"),
                    "resumable": bool(owner and (owner.get("magnet") or owner.get("torrent_url"))),
                    "modified": int(control.stat().st_mtime),
                }
            )
    rows.sort(key=lambda item: -item["modified"])
    return {"roots": roots, "items": rows}


def delete_partial(control: str, remove_target: bool = True) -> Dict[str, Any]:
    path = Path(str(control or ""))
    if not path.is_absolute() or not path.name.endswith(".aria2") or not path.is_file():
        raise ValueError("فایل کنترل .aria2 معتبر نیست")
    target = Path(str(path)[: -len(".aria2")])
    deleted = [str(path)]
    path.unlink()
    if remove_target and target.is_file():
        target.unlink()
        deleted.append(str(target))
    return {"deleted": deleted}


# ------------------------------------------------------------------- server
def daemon_action(action: str) -> Dict[str, Any]:
    handle = aria()
    if action == "start":
        return {"version": handle.ensure_daemon()}
    if action == "stop":
        handle.save_session()
        handle.shutdown()
        return {"stopped": True}
    if action == "save":
        handle.save_session()
        return {"saved": True}
    if action == "purge":
        handle.purge_results()
        return {"purged": True}
    if action == "log":
        if not LOG_FILE.is_file():
            return {"tail": ""}
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"tail": "\n".join(lines[-40:])}
    raise ValueError(f"دستور ناشناخته: {action}")


class Handler(BaseHTTPRequestHandler):
    server_version = "TorrentDesk/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.address_string()}] {fmt % args}")

    # ---- plumbing
    def _json(self, code: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return dict(json.loads(self.rfile.read(length).decode("utf-8") or "{}"))
        except ValueError:
            return {}

    def _guard(self, fn, ok: int = 200) -> None:
        try:
            self._json(ok, {"success": True, "data": fn()})
        except ValueError as exc:
            self._json(400, {"success": False, "error": str(exc)})
        except aria_mod.AriaError as exc:
            self._json(502, {"success": False, "error": f"aria2: {exc}"})
        except Exception as exc:  # noqa: BLE001 - always answer the UI
            self._json(500, {"success": False, "error": f"{type(exc).__name__}: {exc}"})

    # ---- routes
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            if ".." in rel or rel.startswith("/"):
                self.send_error(400)
                return
            candidate = STATIC_DIR / rel
            if not candidate.is_file():
                self.send_error(404)
                return
            content_type = {
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".html": "text/html; charset=utf-8",
            }.get(candidate.suffix, "application/octet-stream")
            self._send_file(candidate, content_type)
            return

        if path == "/api/health":
            handle = aria()
            up = handle.is_up()
            self._json(200, {"success": True, "data": {
                "aria2": {"up": up, "version": handle.version() if up else "",
                          "endpoint": handle.endpoint, "binary": ARIA_BIN},
                "default_dir": prefs().get("default_dir"),
                "port": PORT, "sources": len(sources_mod.SOURCES),
            }})
            return

        if path == "/api/sources":
            self._guard(lambda: {"sources": sources_mod.source_meta()})
            return

        if path == "/api/downloads":
            self._guard(downloads_view)
            return

        if path == "/api/partials":
            root = (query.get("root") or [""])[0]
            depth = int((query.get("depth") or ["3"])[0])
            self._guard(lambda: partials_view(root, depth))
            return

        if path == "/api/settings":
            self._guard(lambda: {
                "prefs": prefs(), "recent_dirs": STORE.recent_dirs(),
                "source_dirs": {row["source"]: row["dir"] for row in STORE.downloads() if row.get("dir")},
                "paths": {"state": str(STATE_FILE), "session": str(SESSION_FILE), "log": str(LOG_FILE)},
                "env": {"timeout": SEARCH_TIMEOUT, "detail_limit": DETAIL_LIMIT, "eztv_pages": EZTV_PAGES},
            })
            return

        if path == "/api/browse":
            target = (query.get("path") or [str(Path.home())])[0]
            self._guard(lambda: browse_dirs(target))
            return

        if path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return

        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        payload = self._body()

        if path == "/api/search":
            query = str(payload.get("query") or "").strip()
            if len(query) < 2:
                self._json(400, {"success": False, "error": "عبارت جست‌وجو خیلی کوتاه است"})
                return
            ids = [str(item) for item in (payload.get("sources") or [])]
            limit = int(payload.get("limit") or 20)
            self._guard(lambda: sources_mod.search_all(query, ids, limit, ctx()))
            return

        if path == "/api/sources/probe":
            self._guard(lambda: {"sources": sources_mod.probe_all(ctx()), "probed_at": int(time.time())})
            return

        if path == "/api/download":
            self._guard(lambda: start_download(payload))
            return

        if path.startswith("/api/downloads/"):
            rest = path[len("/api/downloads/"):]
            raw_id, _, action = rest.partition("/")
            download_id = urllib.parse.unquote(raw_id)
            if action == "redir":
                self._guard(lambda: move_destination(
                    download_id, str(payload.get("dir") or ""), bool(payload.get("create_dir", True))
                ))
                return
            if action == "delete":
                self._guard(lambda: delete_download(download_id, bool(payload.get("remove_files"))))
                return
            self._guard(lambda: control_download(download_id, action))
            return

        if path == "/api/partials/delete":
            self._guard(lambda: delete_partial(
                str(payload.get("control") or ""), bool(payload.get("remove_target", True))
            ))
            return

        if path == "/api/settings":
            patch = {
                key: value for key, value in payload.items()
                if key in ("default_dir", "proxy", "max_concurrent", "file_allocation",
                           "start_paused", "create_dir")
            }
            if patch.get("default_dir"):
                patch["default_dir"] = resolve_dir(str(patch["default_dir"]), create=True)
            self._guard(lambda: {"prefs": STORE.set_prefs(patch)})
            return

        if path == "/api/daemon":
            action = str(payload.get("action") or "")
            self._guard(lambda: daemon_action(action))
            return

        if path == "/api/mkdir":
            parent = resolve_dir(str(payload.get("parent") or ""), create=False)
            name = str(payload.get("name") or "").strip()
            if not name or "/" in name or name in (".", ".."):
                raise ValueError("نام پوشه نامعتبر است")
            created = Path(parent) / name
            created.mkdir(parents=True, exist_ok=True)
            self._guard(lambda: {"path": str(created)})
            return

        self.send_error(404)


def main() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print("GameMap Torrent Desk")
    print(f"  UI      → http://{HOST}:{PORT}")
    print(f"  aria2c  → {ARIA_BIN} · RPC 127.0.0.1:{ARIA_PORT} · bin={shutil.which(ARIA_BIN) or 'NOT FOUND'}")
    print(f"  default → {prefs().get('default_dir')}")
    print(f"  state   → {STATE_FILE}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        aria().save_session()
        print("\nbye (صف در aria2.session ذخیره شد)")


if __name__ == "__main__":
    main()



