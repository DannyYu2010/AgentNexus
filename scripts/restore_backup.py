"""Verify and restore a P6 backup into explicit SQLite paths/Qdrant collection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

from qdrant_client import QdrantClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("backup_dir", type=Path)
    parser.add_argument("--project-db-target", type=Path, required=True)
    parser.add_argument("--history-db-target", type=Path, required=True)
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--qdrant-collection", required=True)
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RuntimeError(f"Restore target already exists: {target}")
    shutil.copy2(source, target)
    with sqlite3.connect(target) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError(f"Restored SQLite is invalid: {target}")


def main() -> None:
    args = parse_args()
    for variable in ("NO_PROXY", "no_proxy"):
        values = {item.strip() for item in os.environ.get(variable, "").split(",") if item}
        os.environ[variable] = ",".join(sorted(values | {"127.0.0.1", "localhost"}))
    manifest = json.loads((args.backup_dir / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        path = args.backup_dir / name
        if path.stat().st_size != expected["size"] or sha256(path) != expected["sha256"]:
            raise RuntimeError(f"Backup checksum mismatch: {name}")

    checked_copy(args.backup_dir / "project_state.db", args.project_db_target)
    checked_copy(args.backup_dir / "mem0_history.db", args.history_db_target)

    snapshots = [
        args.backup_dir / name
        for name in manifest["files"]
        if name.endswith(".snapshot")
    ]
    if len(snapshots) != 1:
        raise RuntimeError("Backup must contain exactly one Qdrant snapshot")

    client = QdrantClient(url=args.qdrant_url)
    try:
        collections = {item.name for item in client.get_collections().collections}
        if args.qdrant_collection in collections:
            if not args.replace:
                raise RuntimeError(
                    f"Restore collection already exists: {args.qdrant_collection}"
                )
            client.delete_collection(args.qdrant_collection)
        with snapshots[0].open("rb") as snapshot:
            client.http.snapshots_api.recover_from_uploaded_snapshot(
                collection_name=args.qdrant_collection,
                wait=True,
                snapshot=snapshot,
            )
        count = client.count(args.qdrant_collection, exact=True).count
    finally:
        client.close()
    print(f"Restored collection {args.qdrant_collection} with {count} points")


if __name__ == "__main__":
    main()
