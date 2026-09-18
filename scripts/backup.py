"""Create an online SQLite + Qdrant Server snapshot backup with checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from qdrant_client import QdrantClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--collection", default="agent_memories")
    return parser.parse_args()


def sqlite_backup(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {destination}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    for variable in ("NO_PROXY", "no_proxy"):
        values = {item.strip() for item in os.environ.get(variable, "").split(",") if item}
        os.environ[variable] = ",".join(sorted(values | {"127.0.0.1", "localhost"}))
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    destination = args.destination or Path("backups") / stamp
    if destination.exists():
        raise RuntimeError(f"Backup destination already exists: {destination}")
    destination.mkdir(parents=True)

    sqlite_backup(args.data_dir / "project_state.db", destination / "project_state.db")
    sqlite_backup(args.data_dir / "mem0_history.db", destination / "mem0_history.db")

    client = QdrantClient(url=args.qdrant_url)
    try:
        snapshot = client.create_snapshot(args.collection, wait=True)
        if snapshot is None:
            raise RuntimeError("Qdrant did not return a snapshot description")
        snapshot_path = destination / snapshot.name
        url = f"{args.qdrant_url}/collections/{args.collection}/snapshots/{snapshot.name}"
        with urllib.request.urlopen(url) as response, snapshot_path.open("wb") as output:
            shutil.copyfileobj(response, output)
        client.delete_snapshot(args.collection, snapshot.name, wait=True)
    finally:
        client.close()

    files = {}
    for path in sorted(destination.iterdir()):
        if path.name == "manifest.json" or not path.is_file():
            continue
        files[path.name] = {"size": path.stat().st_size, "sha256": sha256(path)}
    manifest = {
        "format_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "collection": args.collection,
        "qdrant_url": args.qdrant_url,
        "files": files,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(destination.resolve())


if __name__ == "__main__":
    main()
