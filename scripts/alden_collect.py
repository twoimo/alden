#!/usr/bin/env python3
"""Deterministic adapters for declared local/public source snapshots.

Live site acquisition belongs to the supported host browser/API. This command
consumes its provenance-preserving snapshot without borrowing cookies or sending.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from alden_collection import CollectionStore, encoded, identity
from alden_abort import ABORT_STATE_NAME, AbortToken

MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
BATCH_SIZE = 1000


def json_snapshot(path: Path, *, include_raw=False):
    path = path.absolute()
    if any(p.is_symlink() for p in [*path.parents, path]):
        raise ValueError("collection_source_symlink")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if before.st_size > MAX_SNAPSHOT_BYTES:
            raise ValueError("collection_snapshot_budget")
        raw = source.read(MAX_SNAPSHOT_BYTES + 1)
        after = os.fstat(source.fileno())
    current = path.stat(follow_symlinks=False)
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if signature(before) != signature(after) or signature(after) != signature(current):
        raise RuntimeError("collection_source_changed_during_read")
    result = (json.loads(raw), {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                               "mtime_ns": before.st_mtime_ns, "observed_at": time.time()})
    return (*result, raw) if include_raw else result


def source_graph(target: dict, data: dict, source: dict):
    """Preserve native IDs and explicit edge direction/type; infer nothing."""
    prefix = target["original_id"]
    records, relations, mappings = [], [], {}
    for node in data.get("nodes", []):
        original = prefix + ":node:" + str(node["id"])
        mappings[str(node["id"])] = ("graph", original)
        records.append({"platform": "graph", "original_id": original, "label": node.get("label", node.get("title", str(node["id"]))),
                        "kind": node.get("type", node.get("kind", "source_node")), "text": node.get("summary", ""),
                        "source": source, "source_node_id": str(node["id"]), "raw": node,
                        "truth_status": "source_record; not independently verified"})
    for edge in data.get("edges", []):
        a, b = str(edge["source"]), str(edge["target"])
        if a not in mappings or b not in mappings:
            raise ValueError("collection_source_orphan_edge")
        relations.append({"source_platform": "graph", "source_id": mappings[a][1],
                          "target_platform": "graph", "target_id": mappings[b][1],
                          "type": edge.get("kind", edge.get("type", "reference")),
                          "original_id": str(edge.get("id", "")), "evidence": {"source": source, "raw": edge}})
    return records, relations


def youtube_graph(target: dict, data: dict, source: dict):
    channel = data.get("channel", {})
    if channel.get("id") != target["original_id"]:
        raise ValueError("collection_channel_identity_mismatch")
    records, mappings, relations = [], {}, []
    for series in data.get("series", []):
        original = channel["id"] + ":series:" + str(series["id"])
        mappings["series:" + str(series["id"])] = ("graph", original)
        records.append({"platform": "graph", "original_id": original, "label": series["label"],
                        "kind": "source_group", "text": "", "source": source, "raw": series})
    for video in data.get("videos", []):
        video_id = str(video["id"]).removeprefix("video:")
        mappings["video:" + video_id] = ("youtube", video_id)
        records.append({"platform": "youtube", "original_id": video_id, "label": video["title"],
                        "kind": "video", "text": video.get("description", ""), "author_id": channel["id"],
                        "url": video["url"], "published_at": video.get("date"), "source": source,
                        "raw": video, "coverage": "metadata and supplied evidence; not full-video analysis"})
    for claim in data.get("claims", []):
        claim_id = str(claim["id"])
        original = channel["id"] + ":claim:" + claim_id
        mappings[claim_id] = ("graph", original)
        mappings.setdefault("claim:" + claim_id, ("graph", original))
        records.append({"platform": "graph", "original_id": original, "label": claim.get("title", claim_id),
                        "kind": "source_claim", "text": claim.get("text", ""), "source": source,
                        "url": claim.get("sourceUrl", ""), "evidence_location": claim.get("timestamp"),
                        "truth_status": "source claim; retain stated limits", "raw": claim})
    for edge in data.get("relations", []):
        a, b = edge["source"], edge["target"]
        if a not in mappings or b not in mappings:
            raise ValueError("collection_source_orphan_edge")
        relations.append({"source_platform": mappings[a][0], "source_id": mappings[a][1],
                          "target_platform": mappings[b][0], "target_id": mappings[b][1],
                          "type": edge["type"], "evidence": {"source": source, "raw": edge}})
    return records, relations


def threads_snapshot(target: dict, data: dict, source: dict):
    records, relations = [], []
    for post in data.get("posts", []):
        if post.get("author_id") != target["original_id"]:
            raise ValueError("collection_threads_author_mismatch")
        if post.get("availability", "available") != "available":
            raise RuntimeError("collection_threads_access_not_confirmed")
        records.append({"platform": "threads", "original_id": str(post["id"]),
                        "label": post.get("title", post["text"][:80]), "kind": "post", "text": post["text"],
                        "author_id": post["author_id"], "url": post["url"], "published_at": post.get("published_at"),
                        "source": source, "raw": post})
    return records, relations


def spark_snapshot(target: dict, path: Path):
    """Online Backup of an existing derived index; never the original Kakao DB."""
    path = path.absolute()
    if any(p.is_symlink() for p in [*path.parents, path]):
        raise ValueError("collection_source_symlink")
    with tempfile.TemporaryDirectory(prefix="alden-graph-snapshot-", dir="/private/tmp" if Path("/private/tmp").exists() else None) as tmp:
        snapshot = Path(tmp) / "graph.sqlite3"
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.15) as original:
            original.execute("PRAGMA query_only=ON")
            with sqlite3.connect(snapshot) as dest:
                original.backup(dest, pages=512, sleep=.01)
        snapshot.chmod(0o600)
        with sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True, timeout=.15) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON");db.execute("BEGIN")
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("collection_snapshot_integrity")
            records = []
            namespace = target["original_id"]
            for row in db.execute("SELECT id,path,title,kind,group_id,summary,provenance,hash,content_indexed FROM nodes"):
                data = dict(row)
                records.append({"platform": "graph", "original_id": namespace + ":node:" + row["id"],
                                "label": row["title"], "kind": row["kind"], "text": row["summary"] or "",
                                "source_node_id": row["id"], "source_path": row["path"], "raw": data,
                                "coverage": "existing privacy-filtered derived metadata; original bytes not opened"})
            relations = [{"source_platform": "graph", "source_id": namespace + ":node:" + row["source"],
                          "target_platform": "graph", "target_id": namespace + ":node:" + row["target"],
                          "type": row["type"], "evidence": {"derived_index": str(path), "declared_evidence": row["evidence"]}}
                         for row in db.execute("SELECT source,target,type,evidence FROM edges")]
            source = {"path": str(path), "snapshot_integrity": "ok", "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(), "observed_at": time.time()}
            for record in records:
                record["source"] = source
            return records, relations, source


def collect_target(store: CollectionStore, target_id: str, *, cancelled=lambda: False):
    if cancelled():
        raise RuntimeError("collection_cancelled")
    target = store.target(target_id)
    config = target["config"]
    path = Path(config["path"])
    if config["adapter"] == "spark-index":
        records, relations, source = spark_snapshot(target, path)
    else:
        data, source = json_snapshot(path)
        adapter = {"source-graph": source_graph, "youtube-graph": youtube_graph, "threads-snapshot": threads_snapshot}[config["adapter"]]
        records, relations = adapter(target, data, source)
    # Source acquisition/validation completes before any target checkpoint.
    results = []
    source_revision = source.get("sha256", source.get("snapshot_sha256"))
    for start in range(0, max(1, len(records)), BATCH_SIZE):
        end = min(start + BATCH_SIZE, len(records))
        final = end == len(records)
        results.append(store.ingest(target_id, records[start:end], relations=relations if final else (),
                                    cursor={"source_revision": source_revision, "offset": end, "complete": final},
                                    origin="alden-collector", cancelled=cancelled))
    return {"target_id": target_id, "records": len(records), "relations": len(relations),
            "source": source, "batches": results, "coverage": "declared source snapshot, not a wider account survey"}


def capture_source(store: CollectionStore, target_id: str, *, cancelled=lambda: False):
    """Retain the permitted source bytes and replayable JSON evidence locations."""
    if cancelled():
        raise RuntimeError("collection_cancelled")
    target = store.target(target_id)
    if not target["enabled"]:
        raise RuntimeError("collection_target_paused")
    if not any(p["permission"] != "denied" for p in target["projects"]):
        raise ValueError("collection_source_scope_denied")
    cursor = json.loads(target["cursor"] or "{}")
    if not isinstance(cursor, dict) or cursor.get("complete") is not True:
        raise RuntimeError("collection_source_projection_incomplete")
    config = target["config"]; path = Path(config["path"]); adapter = config["adapter"]
    if adapter == "spark-index":
        records, relations, source = spark_snapshot(target, path)
        data = {"nodes": [r["raw"] for r in records], "relations": relations}
        raw = encoded(data)
        pointers = ["/nodes/" + str(i) for i in range(len(records))]
        scope = "exact bytes of privacy-filtered metadata export; body/protected originals excluded"
    else:
        data, source, raw = json_snapshot(path, include_raw=True)
        if source["sha256"] != cursor.get("source_revision"):
            raise RuntimeError("collection_source_revision_changed")
        handlers = {"source-graph": source_graph, "youtube-graph": youtube_graph, "threads-snapshot": threads_snapshot}
        records, relations = handlers[adapter](target, data, source)
        fields = ["nodes"] if adapter == "source-graph" else ["posts"] if adapter == "threads-snapshot" else ["series", "videos", "claims"]
        pointers = ["/" + key + "/" + str(i) for key in fields for i in range(len(data.get(key, [])))]
        scope = "exact original JSON file bytes"
    if len(records) != len(pointers):
        raise RuntimeError("collection_source_pointer_coverage")
    refs = []
    for record, pointer in zip(records, pointers):
        doc = identity(record["platform"], record["original_id"])
        raw_hash = hashlib.sha256(encoded(record["raw"])).hexdigest()
        refs.append({"document_id": doc, "version": identity("version", doc + ":" + raw_hash),
                     "json_pointer": pointer, "record_sha256": raw_hash})
    manifest = {"schema": 1, "target_id": target_id, "adapter": adapter, "origin": str(path),
                "byte_scope": scope, "processing_version": "alden-source-capture-1", "records": refs}
    if adapter == "spark-index":
        manifest["current_origin_snapshot_sha256"] = source["snapshot_sha256"]
        manifest["prior_projection_origin_revision"] = cursor.get("source_revision")
        manifest["relations"] = {}
        for r in relations:
            a = identity(r["source_platform"], r["source_id"]); b = identity(r["target_platform"], r["target_id"])
            identifier = identity("relation", encoded([target_id, a, r["type"], b, r.get("original_id", "")]).decode())
            manifest["relations"][identifier] = hashlib.sha256(encoded(r["evidence"])).hexdigest()
    return store.retain_source_capture(target_id, raw, manifest, expected_cursor=target["cursor"], cancelled=cancelled)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--target")
    parser.add_argument("--capture-source")
    parser.add_argument("--graph", nargs="*")
    parser.add_argument("--events", action="store_true")
    parser.add_argument("--after", type=int, default=0)
    args = parser.parse_args()
    store = CollectionStore(args.state_root)
    if args.config:
        data, _ = json_snapshot(args.config)
        targets = []
        for config in data["targets"]:
            targets.append(store.register(**config))
        print(json.dumps({"registered": targets}, ensure_ascii=False))
    elif args.capture_source:
        token = AbortToken(args.state_root / ABORT_STATE_NAME)
        print(json.dumps(capture_source(store, args.capture_source, cancelled=token.is_cancelled), ensure_ascii=False))
    elif args.target:
        token = AbortToken(args.state_root / ABORT_STATE_NAME)
        print(json.dumps(collect_target(store, args.target, cancelled=token.is_cancelled), ensure_ascii=False))
    elif args.graph is not None:
        print(json.dumps(store.graph(projects=args.graph), ensure_ascii=False))
    elif args.events:
        print(json.dumps(store.events(after=args.after), ensure_ascii=False))
    else:
        parser.error("select config, target, graph or events")


if __name__ == "__main__":
    main()
