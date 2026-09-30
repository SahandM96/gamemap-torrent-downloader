#!/usr/bin/env python3
"""Torrent Desk self-test — proves the aria2c mechanics work on this machine.

Covers: daemon start · addUri · progress · pause → `.aria2` control file on disk
· gid lost (mimics a daemon restart) · re-add resumes the same partial ·
completion + integrity · partial delete · daemon shutdown.

It serves a localhost file with proper HTTP Range support, so nothing touches
the internet and the resume path is exercised exactly like a real resume.

Run:  python3 selftest.py
"""
from __future__ import annotations

import hashlib
import re
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import aria as aria_mod

HOST = "127.0.0.1"
RPC_PORT = 6813
HTTP_PORT = 8999
FILE_BYTES = 6 * 1024 * 1024
PAYLOAD = ((b"TorrentDesk self-test payload " * 64) * (FILE_BYTES // (29 * 64) + 1))[:FILE_BYTES]
EXPECTED = hashlib.sha256(PAYLOAD).hexdigest()


class RangeHandler(BaseHTTPRequestHandler):
    """Static file server that understands `Range` (what aria2 needs to resume)."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        start, end = 0, FILE_BYTES - 1
        header = self.headers.get("Range") or ""
        match = re.search(r"bytes=(\d*)-(\d*)", header)
        if match:
            if match.group(1):
                start = int(match.group(1))
            if match.group(2):
                end = min(int(match.group(2)), FILE_BYTES - 1)
        body = PAYLOAD[start : end + 1]
        self.send_response(206 if match else 200)
        if match:
            self.send_header("Content-Range", f"bytes {start}-{end}/{FILE_BYTES}")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # aria2 aborts the socket when we pause it — expected

    def log_message(self, *args: object) -> None:
        pass


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        pass


def main() -> int:
    src = Path(tempfile.mkdtemp(prefix="td-src-"))
    dst = Path(tempfile.mkdtemp(prefix="td-dst-"))
    run = Path(tempfile.mkdtemp(prefix="td-run-"))
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")

    server = QuietServer((HOST, HTTP_PORT), RangeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    handle = aria_mod.Aria2(
        port=RPC_PORT, secret="selftest",
        session_file=run / "s.session", log_file=run / "a.log",
        default_dir=str(dst),
    )
    url = f"http://{HOST}:{HTTP_PORT}/payload.bin"

    try:
        version = handle.ensure_daemon()
        check("aria2c daemon up", bool(version), f"v{version} · {handle.endpoint}")
        time.sleep(0.4)

        gid = str(handle.rpc("aria2.addUri", [[url],
                                              {"dir": str(dst), "max-download-limit": "400K"}]))
        check("addUri", bool(gid), gid)
        time.sleep(2.5)
        first = handle.status(gid)
        done = int(first.get("completedLength") or 0)
        check("progress while downloading", 0 < done < FILE_BYTES, f"{done}/{FILE_BYTES} B")

        handle.pause(gid)
        time.sleep(1.2)
        paused = handle.status(gid)
        controls = sorted({p.name for p in dst.glob("*.aria2")})
        check("pause + .aria2 control file",
              str(paused.get("status")) == "paused" and controls == ["payload.bin.aria2"],
              f"status={paused.get('status')} control={controls}")

        handle.remove(gid)  # gid forgotten; partial file + control file stay
        time.sleep(0.8)
        message = ""
        try:
            handle.status(gid)
        except aria_mod.AriaError as exc:
            message = str(exc)
        check("gid forgotten, control file kept",
              "not found" in message.lower() and bool(list(dst.glob("*.aria2"))), message[:70])

        gid2 = str(handle.rpc("aria2.addUri", [[url],
                                               {"dir": str(dst), "continue": "true",
                                                "auto-file-renaming": "false",
                                                "allow-overwrite": "false"}]))
        time.sleep(2.5)
        second = handle.status(gid2)
        error_note = f" · {second.get('errorMessage')}" if second.get("errorCode") not in (None, "0") else ""
        check("re-add resumed the partial",
              int(second.get("completedLength") or 0) >= done,
              f"{done} -> {second.get('completedLength')} B · {second.get('status')}{error_note}")

        deadline = time.time() + 90
        final = second
        while time.time() < deadline:
            final = handle.status(gid2)
            if str(final.get("status")) in ("complete", "error"):
                break
            time.sleep(1.0)
        target = dst / "payload.bin"
        check("download completed",
              str(final.get("status")) == "complete" and target.is_file(),
              f"{final.get('status')} {final.get('errorMessage') or ''}")
        if target.is_file():
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            check("content integrity", actual == EXPECTED, actual[:16])
        time.sleep(1.5)
        check("control file removed on completion", not list(dst.glob("*.aria2")),
              f"dir={sorted(p.name for p in dst.iterdir())}"
              f" · controls={sorted(p.name for p in dst.glob('**/*.aria2'))}")

        half = dst / "orphan.bin"
        half.write_bytes(b"\x00" * 1024)
        control = Path(str(half) + ".aria2")
        control.write_bytes(b"\x01" * 16)
        import torrent_desk

        result = torrent_desk.delete_partial(str(control), remove_target=True)
        check("delete_partial removes target + control",
              not half.exists() and not control.exists(), str(result.get("deleted")))
        try:
            torrent_desk.delete_partial(str(half), True)
            check("delete_partial rejects non-.aria2 path", False, "accepted")
        except ValueError:
            check("delete_partial rejects non-.aria2 path", True)
    finally:
        handle.shutdown()
        server.shutdown()
        shutil.rmtree(src, ignore_errors=True)
        shutil.rmtree(dst, ignore_errors=True)
        shutil.rmtree(run, ignore_errors=True)

    failed = [name for name, ok, _ in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("SELFTEST PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
