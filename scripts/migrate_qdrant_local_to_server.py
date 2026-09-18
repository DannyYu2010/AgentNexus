"""Copy every point from Qdrant Local into a running Qdrant Server."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from qdrant_client import QdrantClient, models


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-path", type=Path, default=Path("data/qdrant"))
    parser.add_argument("--server-url", default="http://127.0.0.1:6333")
    parser.add_argument("--collection", default="agent_memories")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


def migrate(args: argparse.Namespace) -> int:
    _bypass_proxy_for_loopback()
    source = QdrantClient(path=str(args.local_path))
    target = QdrantClient(url=args.server_url)
    try:
        source_names = {item.name for item in source.get_collections().collections}
        if args.collection not in source_names:
            raise RuntimeError(f"Local collection does not exist: {args.collection}")

        target_names = {item.name for item in target.get_collections().collections}
        if args.collection in target_names:
            if not args.replace:
                raise RuntimeError(
                    f"Server collection already exists: {args.collection}; use --replace"
                )
            target.delete_collection(args.collection)

        source_info = source.get_collection(args.collection)
        params = source_info.config.params
        target.create_collection(
            collection_name=args.collection,
            vectors_config=params.vectors,
            sparse_vectors_config=params.sparse_vectors,
            on_disk_payload=params.on_disk_payload,
        )

        copied = 0
        offset = None
        while True:
            points, offset = source.scroll(
                collection_name=args.collection,
                limit=args.batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            if points:
                target.upsert(
                    collection_name=args.collection,
                    points=[
                        models.PointStruct(
                            id=point.id,
                            vector=point.vector,
                            payload=point.payload or {},
                        )
                        for point in points
                    ],
                    wait=True,
                )
                copied += len(points)
            if offset is None:
                break

        target_count = target.count(args.collection, exact=True).count
        if target_count != copied:
            raise RuntimeError(
                f"Migration count mismatch: copied={copied}, server={target_count}"
            )
        return copied
    finally:
        source.close()
        target.close()


def _bypass_proxy_for_loopback() -> None:
    for variable in ("NO_PROXY", "no_proxy"):
        values = {item.strip() for item in os.environ.get(variable, "").split(",") if item}
        os.environ[variable] = ",".join(sorted(values | {"127.0.0.1", "localhost"}))


def main() -> None:
    args = parse_args()
    copied = migrate(args)
    print(f"Migrated {copied} points to {args.server_url}/{args.collection}")


if __name__ == "__main__":
    main()
