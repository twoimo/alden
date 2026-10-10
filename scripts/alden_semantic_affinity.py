"""Bounded read-only cosine candidates for explicit current collection graph nodes.

These candidates are layout evidence only. No embedding service, producer,
identity merge, graph relationship write, or UI integration is invoked here.
"""
from __future__ import annotations

import argparse
from array import array
from collections import Counter
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
import heapq
import json
import math
import os
from pathlib import Path
import re
import resource
import sqlite3
import stat
import struct
import sys
import time

from alden_collection import CollectionStore, MAX_RECORD_BYTES, digest, encoded, identity

MODEL = "mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0"
ENDPOINT = digest(b"http://127.0.0.1:11236/v1/embeddings")
ENCODING = "e5-char256-stride192-weighted-unit-pool-v1"
TEXT_VERSION = "retained-local-original-text-v1"
MAX_NODES = 2048
MAX_INPUT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class E5Profile:
    model: str = MODEL
    endpoint: str = ENDPOINT
    encoding: str = ENCODING
    dimension: int = 384

    def validate(self):
        # Deliberately bind the existing pinned producer, not a guessed model
        # or the first profile found in a mixed vector table.
        if asdict(self) != asdict(E5Profile()):
            raise ValueError("affinity_existing_local_e5_profile_required")


@dataclass(frozen=True)
class Budgets:
    max_nodes: int = MAX_NODES
    max_vector_bytes: int = 8 * 1024 * 1024
    max_text_bytes: int = 32 * 1024 * 1024
    max_source_bytes: int = 64 * 1024 * 1024
    max_pairs: int = 65536
    max_products: int = 25165824
    max_candidates: int = 1024
    neighbors_per_node: int = 3
    wall_seconds: float = 20.0
    cpu_seconds: float = 8.0
    max_rss_bytes: int = 128 * 1024 * 1024

    def validate(self):
        for key, cap in {"max_nodes": MAX_NODES, "max_vector_bytes": 8 * 1024 * 1024,
                         "max_text_bytes": 32 * 1024 * 1024, "max_source_bytes": 64 * 1024 * 1024,
                         "max_pairs": 131072, "max_products": 50331648,
                         "max_candidates": 4096, "neighbors_per_node": 8,
                         "max_rss_bytes": 256 * 1024 * 1024}.items():
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= cap:
                raise ValueError("affinity_budget_invalid:" + key)
        for key, cap in (("wall_seconds", 45), ("cpu_seconds", 20)):
            value = getattr(self, key)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= cap:
                raise ValueError("affinity_budget_invalid:" + key)


class BudgetExceeded(RuntimeError):
    pass


def _rss():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


class _Guard:
    def __init__(self, budgets, cancelled):
        self.budgets, self.cancelled = budgets, cancelled
        self.started, self.cpu_started = time.monotonic(), time.process_time()
        self.counts = Counter()
        self.sql_failure = None

    def check(self):
        if self.cancelled():
            raise RuntimeError("affinity_cancelled")
        if time.monotonic() - self.started >= self.budgets.wall_seconds:
            raise BudgetExceeded("affinity_wall_budget")
        if time.process_time() - self.cpu_started >= self.budgets.cpu_seconds:
            raise BudgetExceeded("affinity_cpu_budget")
        if _rss() > self.budgets.max_rss_bytes:
            raise BudgetExceeded("affinity_rss_budget")

    def charge(self, key, size):
        if type(size) is not int or size < 0:
            raise ValueError("affinity_byte_shape")
        if self.counts[key] + size > getattr(self.budgets, "max_" + key):
            raise BudgetExceeded("affinity_" + key + "_budget")
        self.counts[key] += size
        self.check()

    def progress(self):
        try:
            self.check()
            return 0
        except RuntimeError as error:
            # sqlite3 otherwise reduces an exception in this callback to the
            # opaque OperationalError('interrupted'). Preserve the exact stop.
            self.sql_failure = error
            return 1


@contextmanager
def _sql_errors(guard):
    try:
        yield
    except sqlite3.Error:
        if guard.sql_failure is not None:
            raise guard.sql_failure
        raise


def _json(raw):
    def reject(_):
        raise ValueError("affinity_nonfinite_json")
    try:
        return json.loads(raw, parse_constant=reject)
    except RecursionError as error:
        # Malformed record data must not discard unrelated usable nodes.
        # Keep resource/cancellation RuntimeErrors outside this conversion.
        raise ValueError("affinity_json_nesting") from error


def _regular_path(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("affinity_symlink")
    if not path.is_file():
        raise ValueError("affinity_existing_file_required")
    for suffix in ("-wal", "-shm"):
        if Path(str(path) + suffix).is_symlink():
            raise ValueError("affinity_symlink")


def _sqlite_preflight(path):
    _regular_path(path)
    with path.open("rb") as handle:
        header = handle.read(20)
    if len(header) != 20 or header[:16] != b"SQLite format 3\x00":
        raise ValueError("affinity_sqlite_header_invalid")
    if header[18:20] == b"\x02\x02":
        # SQLite mode=ro can create WAL sidecars in a writable directory. Do
        # not initialize a closed WAL database as a side effect of this read.
        # Do not use immutable=1: it would ignore a live WAL and its revisions.
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            if not sidecar.is_file():
                raise ValueError("affinity_wal_sidecars_required:" + path.name)
            _regular_path(sidecar)


def _source(folder, name, guard):
    if not isinstance(name, str) or not re.fullmatch(r"[0-9a-f]{64}\.json", name):
        raise ValueError("affinity_source_name")
    _regular_path(folder / name)
    parent = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_RECORD_BYTES:
                raise ValueError("affinity_source_shape")
            guard.charge("source_bytes", before.st_size)
            raw = handle.read(MAX_RECORD_BYTES + 1)
            after = os.fstat(handle.fileno())
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError("affinity_source_changed")
        if len(raw) != before.st_size or digest(raw) + ".json" != name:
            raise ValueError("affinity_source_hash")
        return _json(raw)
    finally:
        os.close(parent)


def _inputs(projects, nodes, budgets):
    if (not isinstance(projects, (list, tuple)) or not 1 <= len(projects) <= 16
            or any(not isinstance(p, str) or not 1 <= len(p) <= 128 for p in projects)):
        raise ValueError("affinity_project_scope_required")
    if not isinstance(nodes, (list, tuple)) or len(nodes) > budgets.max_nodes:
        raise ValueError("affinity_node_budget")
    refs, seen = [], set()
    for node in nodes:
        if (not isinstance(node, dict) or any(not isinstance(node.get(k), str)
                or not 1 <= len(node[k]) <= 256 for k in ("id", "source_version", "source_target"))):
            raise ValueError("affinity_current_graph_node_required")
        if node["id"] in seen:
            raise ValueError("affinity_duplicate_node")
        seen.add(node["id"])
        refs.append({k: node[k] for k in ("id", "source_version", "source_target")})
    return sorted(set(projects)), sorted(refs, key=lambda r: r["id"])


def _load(db, cache, store, node, projects, profile, guard):
    marks = ",".join("?" for _ in projects)
    scope = db.execute("""SELECT p.project,p.permission FROM target_projects p JOIN memberships m
        ON m.target_id=p.target_id JOIN documents d ON d.id=m.document_id
        WHERE m.document_id=? AND m.target_id=? AND m.availability='available'
        AND d.availability='available' AND p.permission!='denied' AND p.project IN (""" + marks + ") ORDER BY p.project",
        (node["id"], node["source_target"], *projects)).fetchall()
    if not scope:
        return None, "not_current_or_permitted"
    # The requested graph target's current membership wins over the global
    # document version, which can belong to an unrelated newer project.
    head = db.execute("""SELECT m.current_version,v.document_id,
        length(CAST(v.label AS BLOB))+length(CAST(v.body AS BLOB))+length(CAST(v.metadata AS BLOB))+
        length(CAST(v.raw_sha256 AS BLOB))+length(CAST(v.raw_path AS BLOB))+
        length(CAST(v.processing_version AS BLOB))+length(CAST(v.projection_sha256 AS BLOB))+
        length(CAST(d.platform AS BLOB))+length(CAST(d.original_id AS BLOB)),
        typeof(v.label),typeof(v.body),typeof(v.metadata)
        FROM memberships m LEFT JOIN versions v ON v.id=m.current_version
        JOIN documents d ON d.id=m.document_id
        WHERE m.document_id=? AND m.target_id=?""", (node["id"], node["source_target"])).fetchone()
    if head[0] != node["source_version"]:
        return None, "stale_graph_version"
    if head[1] != node["id"] or type(head[2]) is not int or tuple(head[3:]) != ("text", "text", "text"):
        return None, "malformed_current_version"
    if head[2] > MAX_RECORD_BYTES:
        return None, "oversized_current_text"
    guard.charge("text_bytes", head[2])
    row = db.execute("""SELECT d.platform,d.original_id,v.* FROM documents d JOIN versions v
        ON v.document_id=d.id WHERE d.id=? AND v.id=?""", (node["id"], node["source_version"])).fetchone()
    try:
        metadata = _json(row["metadata"])
        if not isinstance(metadata, dict):
            raise ValueError("metadata_shape")
        projection = digest(encoded([row["label"], row["body"], {k: v for k, v in metadata.items() if k != "source"}]))
        expected = identity("version", encoded([node["id"], row["raw_sha256"], row["processing_version"], projection]).decode())
        if identity(row["platform"], row["original_id"]) != node["id"] or projection != row["projection_sha256"] or expected != node["source_version"]:
            return None, "current_version_hash_mismatch"
    except (TypeError, ValueError, RecursionError):
        return None, "malformed_current_version"
    vector_meta = cache.execute("""SELECT CASE WHEN typeof(text_hash)='text' AND length(text_hash)=64
        THEN text_hash END,model=? AND endpoint=? AND encoding=?,typeof(vector),length(vector)
        FROM vectors WHERE document_id=? AND version=?""",
        (profile.model, profile.endpoint, profile.encoding, node["id"], node["source_version"])).fetchone()
    if vector_meta is None:
        return None, "missing_current_vector"
    if vector_meta[1] != 1:
        return None, "different_profile"
    if vector_meta[2] != "blob" or type(vector_meta[3]) is not int or vector_meta[3] % 4:
        return None, "malformed_vector"
    if vector_meta[3] != profile.dimension * 4:
        return None, "different_dimension"
    text_size = cache.execute("""SELECT length(CAST(label AS BLOB))+length(CAST(body AS BLOB))+
        length(CAST(base_hash AS BLOB))+length(CAST(raw_sha256 AS BLOB))+
        length(CAST(extraction AS BLOB))+length(CAST(body_source AS BLOB))
        FROM texts WHERE document_id=? AND version=?""", (node["id"], node["source_version"])).fetchone()
    if text_size is None:
        return None, "missing_bound_text"
    if type(text_size[0]) is not int or text_size[0] > MAX_RECORD_BYTES:
        return None, "malformed_bound_text"
    guard.charge("text_bytes", text_size[0])
    text = cache.execute("""SELECT base_hash,raw_sha256,extraction,label,body,body_source
        FROM texts WHERE document_id=? AND version=?""", (node["id"], node["source_version"])).fetchone()
    if (tuple(text[:4]) != (digest((row["label"] + "\n" + row["body"]).encode()), row["raw_sha256"], TEXT_VERSION, row["label"])
            or not isinstance(text[4], str) or text[5] not in {"stored_body", "retained_record.localOriginalText"}):
        return None, "stale_text_binding"
    try:
        source = _source(store.blobs, row["raw_path"], guard)
        if row["raw_path"] != row["raw_sha256"] + ".json":
            return None, "source_hash_mismatch"
    except (OSError, ValueError):
        return None, "malformed_source"
    if text[5] == "stored_body":
        if text[4] != row["body"]:
            return None, "stale_text_binding"
    elif (row["platform"] != "graph" or not isinstance(source, dict)
          or not isinstance(source.get("localOriginalText"), str)
          or not source["localOriginalText"].strip() or source["localOriginalText"] != text[4]):
        return None, "retained_text_source_mismatch"
    complete_text = row["label"] + "\n" + text[4]
    if not complete_text.strip():
        return None, "empty_text"
    text_hash = digest(complete_text.encode())
    if vector_meta[0] != text_hash:
        return None, "stale_vector_text_hash"
    guard.charge("vector_bytes", vector_meta[3])
    raw = cache.execute("SELECT vector FROM vectors WHERE document_id=? AND version=?",
                        (node["id"], node["source_version"])).fetchone()[0]
    values = struct.unpack("<" + "f" * profile.dimension, raw)
    if not all(math.isfinite(v) for v in values):
        return None, "nonfinite_vector"
    norm = math.sqrt(sum(v * v for v in values))
    if not math.isfinite(norm) or abs(norm - 1) > .001:
        return None, "nonunit_vector"
    proof = {**node, "projects": [r[0] for r in scope],
             "permissions": [{"project": r[0], "permission": r[1]} for r in scope],
             "source_platform": row["platform"],
             "original_id_sha256": digest(row["original_id"].encode()), "raw_sha256": row["raw_sha256"],
             "raw_path": row["raw_path"], "processing_version": row["processing_version"],
             "projection_sha256": projection, "base_text_sha256": text[0], "text_sha256": text_hash,
             "body_source": text[5], "vector_sha256": digest(raw), "dimension": profile.dimension,
             "vector_norm": norm, "profile_sha256": digest(encoded(asdict(profile))),
             "source_verified": "retained canonical record JSON; not original full export/media"}
    return (proof, array("f", values), norm), None


def _eligible_pairs(proofs, guard):
    groups = list(Counter(frozenset(p["projects"]) for p in proofs).items())
    count = 0
    for i, (scope, size) in enumerate(groups):
        guard.check()
        count += size * (size - 1) // 2
        for other, other_size in groups[i + 1:]:
            if not scope.isdisjoint(other):
                count += size * other_size
    return count


def _candidates(loaded, budgets, threshold, guard):
    heaps = [[] for _ in loaded]
    proofs = [entry[0] for entry in loaded]
    eligible = _eligible_pairs(proofs, guard)
    pair_cap = min(budgets.max_pairs, budgets.max_products // E5Profile().dimension)
    evaluated = passing = 0
    stop = False
    for i, (left, x, nx) in enumerate(loaded):
        guard.check()
        for j in range(i + 1, len(loaded)):
            right, y, ny = loaded[j]
            if set(left["projects"]).isdisjoint(right["projects"]):
                continue
            if evaluated >= pair_cap:
                stop = True
                break
            if evaluated % 16 == 0:
                guard.check()
            score = max(-1., min(1., sum(a * b for a, b in zip(x, y)) / (nx * ny)))
            evaluated += 1
            if score < threshold:
                continue
            passing += 1
            for owner, neighbor in ((i, j), (j, i)):
                item = (score, neighbor)
                if len(heaps[owner]) < budgets.neighbors_per_node:
                    heapq.heappush(heaps[owner], item)
                elif item > heaps[owner][0]:
                    heapq.heapreplace(heaps[owner], item)
        if stop:
            break
    pairs = {}
    for i, heap in enumerate(heaps):
        for score, j in heap:
            pairs[min(i, j), max(i, j)] = score
    ranked = sorted(pairs, key=lambda key: (-pairs[key], proofs[key[0]]["id"], proofs[key[1]]["id"]))
    candidates = [{"kind": "semantic_layout_affinity", "source": proofs[i]["id"], "target": proofs[j]["id"],
                   "cosine": pairs[i, j], "projects": sorted(set(proofs[i]["projects"]) & set(proofs[j]["projects"]))}
                  for i, j in ranked[:budgets.max_candidates]]
    return candidates, {"possible_vector_pairs": len(loaded) * (len(loaded) - 1) // 2,
        "eligible_same_project_pairs": eligible, "pairs_evaluated": evaluated,
        "dimension_products": evaluated * E5Profile().dimension, "pairs_above_threshold": passing,
        "neighbor_union_candidates": len(ranked), "candidates_returned": len(candidates),
        "candidates_truncated": max(0, len(ranked) - budgets.max_candidates),
        "pair_coverage": evaluated / eligible if eligible else None,
        "pair_budget_exhausted": evaluated < eligible}


def affinity(root, projects, nodes, *, profile=E5Profile(), budgets=Budgets(),
             min_cosine=.65, cancelled=lambda: False):
    """Consume explicit graph node references; return scoped layout candidates.

    The caller supplies its actual visible node set and revalidates versions on
    consumption. This API does not fetch an unbounded graph or update positions.
    """
    profile.validate()
    budgets.validate()
    projects, nodes = _inputs(projects, nodes, budgets)
    if type(min_cosine) not in (int, float) or not math.isfinite(min_cosine) or not -1 <= min_cosine <= 1:
        raise ValueError("affinity_threshold_invalid")
    guard = _Guard(budgets, cancelled)
    guard.check()
    collection_root = Path(root).absolute() / "knowledge" / "collection"
    # Preflight both before even opening the CollectionStore. A refusal is a
    # failure, not a zero-vector success or permission to modify the original.
    for name in ("collection.sqlite3", "retrieval.sqlite3"):
        _sqlite_preflight(collection_root / name)
    store = CollectionStore.open_existing(Path(root))
    if store is None or store.schema != 3:
        raise ValueError("affinity_existing_v3_store_required")
    cache_path = store.root / "retrieval.sqlite3"
    _regular_path(cache_path)
    with _sql_errors(guard), store.database() as db, closing(sqlite3.connect(cache_path.as_uri() + "?mode=ro", uri=True, timeout=.15)) as cache:
        cache.execute("PRAGMA query_only=ON")
        cache.execute("BEGIN")
        # Connection-local cache caps; no journal mode, checkpoint, index or DDL.
        for connection in (db, cache):
            connection.execute("PRAGMA cache_size=-2048")
            connection.set_progress_handler(guard.progress, 1000)
        return _affinity_on_reads(store, db, cache, projects, nodes, profile=profile,
                                  budgets=budgets, min_cosine=min_cosine, guard=guard)


def _validate_reads(store, db, cache):
    """Verify the borrowed transaction boundary without changing caller state.

    The product adapter attests mode=ro when opening these exact derived files.
    This helper also requires query-only active transactions and exact main DBs;
    it never turns an arbitrary writer into a purported read-only connection.
    """
    if not store.read_only or store.schema != 3 or db.row_factory is not sqlite3.Row:
        raise ValueError("affinity_verified_read_transactions_required")
    for connection, path in ((db, store.path), (cache, store.root / "retrieval.sqlite3")):
        if not connection.in_transaction or connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise ValueError("affinity_verified_read_transactions_required")
        databases = connection.execute("PRAGMA database_list").fetchall()
        main = [r for r in databases if r[1] == "main"]
        if (len(main) != 1 or Path(main[0][2]).absolute() != path
                or any(r[1] != "main" and (r[1] != "temp" or r[2]) for r in databases)):
            raise ValueError("affinity_read_transaction_path_mismatch")


def _affinity_on_reads(store, db, cache, projects, nodes, *, profile=E5Profile(),
                       budgets=Budgets(), min_cosine=.65, guard=None):
    """Shared evaluator for the strict CLI and attested product-owned readers.

    No connection open/close, transaction begin/commit, checkpoint or write is
    performed here. The caller owns both lifetimes and SQL progress callbacks.
    """
    profile.validate()
    budgets.validate()
    projects, nodes = _inputs(projects, nodes, budgets)
    if type(min_cosine) not in (int, float) or not math.isfinite(min_cosine) or not -1 <= min_cosine <= 1:
        raise ValueError("affinity_threshold_invalid")
    guard = guard if guard is not None else _Guard(budgets, lambda: False)
    if guard.budgets != budgets:
        raise ValueError("affinity_guard_budget_mismatch")
    guard.check()
    _validate_reads(store, db, cache)
    tables = {r[0] for r in cache.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"vectors", "texts"} <= tables:
        raise ValueError("affinity_existing_vector_text_tables_required")
    rejected, loaded = Counter(), []
    journal = db.execute("SELECT substr(value,1,128) FROM meta WHERE key='journal_id'").fetchone()
    for node in nodes:
        guard.check()
        entry, reason = _load(db, cache, store, node, projects, profile, guard)
        if entry is None:
            rejected[reason] += 1
        else:
            loaded.append(entry)
    candidates, pairs = _candidates(loaded, budgets, min_cosine, guard)
    guard.check()
    proofs = [entry[0] for entry in loaded]
    complete = len(proofs) == len(nodes) and not pairs["pair_budget_exhausted"] and not pairs["candidates_truncated"]
    return {"schema": "alden-semantic-affinity-v1", "state": "bounded_ready" if complete else "bounded_partial",
            "purpose": "layout_evidence_only", "identity_merge": False, "factual_relationship": False,
            "projects": projects, "profile": asdict(profile), "profile_sha256": digest(encoded(asdict(profile))),
            "input_sha256": digest(encoded(nodes)), "journal_id": journal[0] if journal else None,
            "snapshot_basis": "independent read transactions; exact target/version/raw/base/text bindings",
            "coverage": {"requested_nodes": len(nodes), "usable_vector_nodes": len(proofs),
                         "rejected_nodes": len(nodes) - len(proofs), "reasons": dict(sorted(rejected.items())),
                         "node_coverage": len(proofs) / len(nodes) if nodes else None,
                         "usable_by_project": dict(sorted(Counter(p for proof in proofs for p in proof["projects"]).items())),
                         "cross_project_pairs_excluded": pairs["possible_vector_pairs"] - pairs["eligible_same_project_pairs"], **pairs},
            "budgets": asdict(budgets), "min_cosine": min_cosine, "nodes": proofs, "candidates": candidates,
            "metrics": {**guard.counts, "wall_seconds": time.monotonic() - guard.started,
                        "cpu_seconds": time.process_time() - guard.cpu_started, "process_peak_rss_bytes": _rss()},
            "limitations": ["only supplied graph nodes, not whole project/graph coverage",
                "no service liveness or current loaded weights verification",
                "similarity is not identity, factual relation, semantic gold or independent-source proof",
                "read-time versions can change; consumer must revalidate",
                "WAL sidecars must already exist; concurrent sidecar removal after preflight is not fenced",
                "pair traversal is deterministic ID order; truncated pairs are not global nearest neighbors",
                "neighbor union bounds outgoing selections; incoming degree can exceed neighbors_per_node",
                "no UI placement, rest-state, build, install or delivery integration verified"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--project", action="append", required=True)
    parser.add_argument("--nodes-json", required=True, type=Path,
                        help="JSON list of id/source_version/source_target; graph_page nodes also accepted")
    parser.add_argument("--max-pairs", type=int, default=Budgets().max_pairs)
    parser.add_argument("--max-products", type=int, default=Budgets().max_products)
    parser.add_argument("--max-candidates", type=int, default=Budgets().max_candidates)
    parser.add_argument("--neighbors", type=int, default=Budgets().neighbors_per_node)
    parser.add_argument("--min-cosine", type=float, default=.65)
    args = parser.parse_args(argv)
    try:
        _regular_path(args.nodes_json.absolute())
        with args.nodes_json.open("rb") as handle:
            raw = handle.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("affinity_input_byte_budget")
        nodes = _json(raw)
        if isinstance(nodes, dict):
            nodes = nodes.get("nodes")
        result = affinity(args.state_root, args.project, nodes,
                          budgets=Budgets(max_pairs=args.max_pairs, max_products=args.max_products,
                                          max_candidates=args.max_candidates, neighbors_per_node=args.neighbors),
                          min_cosine=args.min_cosine)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        # Failure emits no partial candidates. In particular a hard runtime or
        # memory stop cannot be mistaken for successful completed coverage.
        print(json.dumps({"ok": False, "state": "failed", "error": str(error), "error_type": type(error).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
