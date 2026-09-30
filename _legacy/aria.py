#!/usr/bin/env python3
"""Torrent Desk — aria2c daemon lifecycle + JSON-RPC client (stdlib only).

One long-lived `aria2c --enable-rpc` daemon owns every download. That is what
gives us real pause/resume/progress for half-downloaded files, a per-download
destination folder (`dir` option of aria2.addUri) and a session file so the
queue survives a restart.
"""
from __future__ import annotations

import base64
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

STATUS_KEYS = [
    "gid",
    "status",
    "totalLength",
    "completedLength",
    "uploadLength",
    "downloadSpeed",
    "uploadSpeed",
    "connections",
    "numSeeders",
    "seeder",
    "errorCode",
    "errorMessage",
    "dir",
    "files",
    "bittorrent",
    "infoHash",
    "followedBy",
    "verifiedLength",
]


class AriaError(RuntimeError):
    """JSON-RPC level failure (bad GID, daemon down, ...)."""

    def __init__(self, message: str, code: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code

    @property
    def is_unknown_gid(self) -> bool:
        return self.code == 1 or "is not found" in str(self).lower()


class Aria2:
    """Thin client for one managed aria2c daemon."""

    def __init__(
        self,
        *,
        binary: str = "aria2c",
        host: str = "127.0.0.1",
        port: int = 6811,
        secret: str = "",
        session_file: Path,
        log_file: Path,
        default_dir: str,
        max_concurrent: int = 3,
        file_allocation: str = "none",
        proxy: str = "",
        extra_args: Iterable[str] = (),
    ) -> None:
        self.binary = binary
        self.host = host
        self.port = port
        self.secret = secret
        self.session_file = Path(session_file)
        self.log_file = Path(log_file)
        self.default_dir = default_dir
        self.max_concurrent = max_concurrent
        self.file_allocation = file_allocation
        self.proxy = proxy
        self.extra_args = [a for a in extra_args if a]

    # ------------------------------------------------------------- rpc
    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}/jsonrpc"

    def rpc(self, method: str, params: Optional[List[Any]] = None, timeout: float = 20.0) -> Any:
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": "desk", "method": method}
        args: List[Any] = [f"token:{self.secret}"] if self.secret else []
        args.extend(params or [])
        payload["params"] = args
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # aria2 answers JSON-RPC errors with HTTP 400 + a proper error body.
            detail = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(detail)
                error = parsed.get("error") or {}
                raise AriaError(str(error.get("message") or f"HTTP {exc.code}"),
                                error.get("code")) from exc
            except ValueError:
                raise AriaError(f"aria2 HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise AriaError(f"aria2 RPC unreachable ({exc.reason})") from exc
        except (ValueError, OSError) as exc:
            raise AriaError(f"aria2 RPC failed: {exc}") from exc
        if "error" in body:
            error = body["error"] or {}
            raise AriaError(str(error.get("message") or "aria2 error"), error.get("code"))
        return body.get("result")

    # -------------------------------------------------------- daemon
    def version(self) -> str:
        result = self.rpc("aria2.getVersion", timeout=4)
        return str((result or {}).get("version") or "?")

    def is_up(self) -> bool:
        try:
            self.version()
            return True
        except AriaError:
            return False

    def daemon_args(self) -> List[str]:
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        args = [
            self.binary,
            "--enable-rpc",
            f"--rpc-listen-port={self.port}",
            "--rpc-listen-all=false",
            f"--rpc-secret={self.secret}",
            "--continue=true",
            "--auto-file-renaming=false",
            "--allow-overwrite=false",
            "--seed-time=0",
            "--bt-enable-lpd=true",
            "--enable-dht=true",
            "--dht-listen-port=6881",
            "--listen-port=6881",
            f"--max-concurrent-downloads={self.max_concurrent}",
            f"--file-allocation={self.file_allocation}",
            f"--dir={self.default_dir}",
            f"--save-session={self.session_file}",
            "--save-session-interval=30",
            "--summary-interval=0",
            "--console-log-level=warn",
            f"--log={self.log_file}",
            "--quiet=true",
            "--daemon=true",
        ]
        if self.proxy:
            args.append(f"--all-proxy={self.proxy}")
        if self.session_file.is_file() and self.session_file.stat().st_size:
            args.append(f"--input-file={self.session_file}")
        args.extend(self.extra_args)
        return args

    def ensure_daemon(self, wait: float = 8.0) -> str:
        if self.is_up():
            self.sync_globals()
            return self.version()
        proc = subprocess.run(self.daemon_args(), capture_output=True, check=False, timeout=30)
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.is_up():
                self.sync_globals()
                return self.version()
            time.sleep(0.35)
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise AriaError(f"aria2c RPC on {self.endpoint} not answering. {detail or self.log_file}")

    def sync_globals(self) -> None:
        """Re-apply the knobs the UI may have changed."""
        options: Dict[str, Any] = {
            "max-concurrent-downloads": str(self.max_concurrent),
            "file-allocation": self.file_allocation,
            "all-proxy": self.proxy or "",
        }
        try:
            self.rpc("aria2.changeGlobalOption", [options])
        except AriaError:
            pass

    def shutdown(self, force: bool = True) -> None:
        method = "aria2.forceShutdown" if force else "aria2.shutdown"
        try:
            self.rpc(method, timeout=6)
        except AriaError:
            pass

    def save_session(self) -> None:
        try:
            self.rpc("aria2.saveSession", timeout=6)
        except AriaError:
            pass

    def global_stat(self) -> Dict[str, Any]:
        try:
            return dict(self.rpc("aria2.getGlobalStat", timeout=6) or {})
        except AriaError as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------- mutation
    def add_magnet(self, magnet: str, directory: str, paused: bool = False) -> str:
        options = {"dir": directory, "pause": "true" if paused else "false"}
        return str(self.rpc("aria2.addUri", [[magnet], options]))

    def add_torrent(self, torrent_bytes: bytes, directory: str, paused: bool = False) -> str:
        options = {"dir": directory, "pause": "true" if paused else "false"}
        encoded = base64.b64encode(torrent_bytes).decode("ascii")
        return str(self.rpc("aria2.addTorrent", [encoded, [], options]))

    def pause(self, gid: str) -> None:
        self.rpc("aria2.pause", [gid])

    def unpause(self, gid: str) -> None:
        self.rpc("aria2.unpause", [gid])

    def remove(self, gid: str) -> None:
        """Stop the download and forget the gid — files/partial files stay on disk."""
        try:
            self.rpc("aria2.forceRemove", [gid])
        except AriaError as exc:
            if not exc.is_unknown_gid:
                raise

    def purge_result(self, gid: str) -> None:
        try:
            self.rpc("aria2.removeDownloadResult", [gid])
        except AriaError:
            pass

    def purge_results(self) -> None:
        try:
            self.rpc("aria2.purgeDownloadResult")
        except AriaError:
            pass

    # ---------------------------------------------------------- reads
    def status(self, gid: str) -> Dict[str, Any]:
        return dict(self.rpc("aria2.tellStatus", [gid, STATUS_KEYS]))

    def all_status(self) -> Dict[str, Dict[str, Any]]:
        """gid -> status row for everything the daemon still knows about."""
        rows: Dict[str, Dict[str, Any]] = {}
        queries = (
            ("aria2.tellActive", [STATUS_KEYS]),
            ("aria2.tellWaiting", [0, 500, STATUS_KEYS]),
            ("aria2.tellStopped", [0, 300, STATUS_KEYS]),
        )
        for method, params in queries:
            try:
                result = self.rpc(method, params) or []
            except AriaError:
                continue
            for row in result:
                gid = str(row.get("gid") or "")
                if gid:
                    rows[gid] = dict(row)
        return rows


def describe_files(status: Dict[str, Any]) -> List[Dict[str, Any]]:
    """aria2 `files` array -> compact rows for the UI."""
    out: List[Dict[str, Any]] = []
    for item in status.get("files") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        out.append(
            {
                "path": path,
                "name": path.rsplit("/", 1)[-1],
                "length": int(item.get("length") or 0),
                "completed": int(item.get("completedLength") or 0),
                "selected": str(item.get("selected")) != "false",
            }
        )
    return out[:500]
