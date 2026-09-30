#!/usr/bin/env python3
"""Bridge for Rust UI: runs legacy search adapters (v0.1). Stdlib only."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGACY = ROOT / "_legacy"
sys.path.insert(0, str(LEGACY))

import sources  # noqa: E402


def main() -> None:
    req = json.load(sys.stdin)
    cmd = req.get("cmd")
    ctx = sources.Ctx(
        proxy=str(req.get("proxy") or ""),
        timeout=float(req.get("timeout") or 14),
        detail_limit=int(req.get("detail_limit") or 6),
        eztv_pages=int(req.get("eztv_pages") or 4),
    )
    if cmd == "meta":
        out = {"sources": sources.source_meta()}
    elif cmd == "search":
        q = str(req.get("query") or "").strip()
        ids = [str(x) for x in (req.get("sources") or [])]
        limit = int(req.get("limit") or 20)
        out = sources.search_all(q, ids, limit, ctx)
    elif cmd == "probe":
        out = {"sources": sources.probe_all(ctx), "probed_at": int(time.time())}
    else:
        print(json.dumps({"error": f"unknown cmd {cmd}"}), file=sys.stderr)
        sys.exit(1)
    json.dump(out, sys.stdout, ensure_ascii=False)
    sys.stdout.flush()
    # Hard exit: search_all may leave over-budget worker threads behind.
    os._exit(0)


if __name__ == "__main__":
    main()
