"""Product-only layout affinity over caller-owned derived read transactions.

The strict semantic developer CLI remains unchanged. This product boundary
explicitly permits SQLite's ordinary WAL/SHM read metadata on the two derived
databases, never record/index changes, journal changes, checkpoints or originals.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import threading
import time

from alden_collection import CollectionStore, digest, encoded
import alden_semantic_affinity as semantic

MAX_QUERY_BYTES = 4096
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_COLLECTION_PAGE = 1984
MAX_DISPLAY_NODES = 2048
PROFILE = semantic.E5Profile()
PROFILE_SHA256 = digest(encoded(asdict(PROFILE)))
QUERY_KEYS = {"projects", "limit", "offset", "target_id", "platform", "node_type", "relation",
              "search", "since", "until", "focus", "hops", "overview", "details", "expected_version"}
_ATTESTATION = object()
_WRITE_ACTIONS = {getattr(sqlite3, name) for name in (
    "SQLITE_INSERT", "SQLITE_UPDATE", "SQLITE_DELETE", "SQLITE_CREATE_INDEX", "SQLITE_CREATE_TABLE",
    "SQLITE_CREATE_TEMP_INDEX", "SQLITE_CREATE_TEMP_TABLE", "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW",
    "SQLITE_CREATE_TRIGGER", "SQLITE_CREATE_VIEW", "SQLITE_DROP_INDEX", "SQLITE_DROP_TABLE",
    "SQLITE_DROP_TEMP_INDEX", "SQLITE_DROP_TEMP_TABLE", "SQLITE_DROP_TEMP_TRIGGER", "SQLITE_DROP_TEMP_VIEW",
    "SQLITE_DROP_TRIGGER", "SQLITE_DROP_VIEW", "SQLITE_ALTER_TABLE", "SQLITE_REINDEX", "SQLITE_ANALYZE",
    "SQLITE_ATTACH", "SQLITE_DETACH", "SQLITE_CREATE_VTABLE", "SQLITE_DROP_VTABLE")}
_PROOF_KEYS = ("id", "source_version", "source_target", "projects", "raw_sha256", "processing_version",
               "raw_path", "projection_sha256", "base_text_sha256", "text_sha256", "body_source", "vector_sha256",
               "vector_norm", "permissions", "dimension", "profile_sha256")


def _sidecars(folder):
    result = {}
    for name in ("collection.sqlite3", "retrieval.sqlite3"):
        result[name] = {}
        for suffix in ("-wal", "-shm"):
            path = folder / (name + suffix)
            try:
                info = path.stat()
                result[name][suffix] = {"exists": True, "bytes": info.st_size, "mtime_ns": info.st_mtime_ns}
            except FileNotFoundError:
                result[name][suffix] = {"exists": False}
    return result


class _BorrowedCollection(CollectionStore):
    @contextmanager
    def database(self, *, publication_guard=None):
        if publication_guard is not None:
            raise ValueError("layout_read_publication_guard_forbidden")
        self._owner.validate()
        # Nested graph_page/projects/targets calls reuse the owner's snapshot.
        # They never commit/rollback/close or replace the shared connection.
        yield self._owner.collection


class ReadTransactions:
    """Attested mode=ro owner; open using read_transactions(), not raw writers."""
    def __init__(self, state_root, guard):
        self.root = Path(state_root).absolute()
        self.folder = self.root / "knowledge" / "collection"
        self.guard, self.thread = guard, threading.get_ident()
        self.collection = self.cache = self.store = None
        self.active, self._attestation = False, _ATTESTATION
        self.metadata = {"policy": "derived SQLite normal WAL/SHM read metadata allowed; record/index writes forbidden",
                         "before": _sidecars(self.folder), "after": None, "denied_write_attempts": 0,
                         "shm_byte_identity_measured": False, "main_content_identity_measured": False}

    def _authorize(self, action, arg1, arg2, database, trigger):
        allowed_pragma = action != sqlite3.SQLITE_PRAGMA or (
            arg1 in {"query_only", "database_list", "table_info", "cache_size", "foreign_keys", "data_version"}
            and (arg2 is None or (arg1 == "cache_size" and arg2 == "-2048")
                 or (arg1 == "foreign_keys" and arg2 == "ON")))
        if action in _WRITE_ACTIONS or not allowed_pragma or (
                action == sqlite3.SQLITE_FUNCTION and arg2 == "load_extension"):
            self.metadata["denied_write_attempts"] += 1
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def _connect(self, path, *, rows=False):
        self.guard.check()
        semantic._regular_path(path)
        # No immutable/nolock, no copy, no journal_mode or checkpoint operation.
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.15)
        try:
            if rows:
                db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA cache_size=-2048")
            db.execute("BEGIN")
            db.set_authorizer(self._authorize)
            db.set_progress_handler(self.guard.progress, 1000)
            # Pin this database's snapshot. Collection and cache are separate
            # snapshots; matching current version/raw/base/text binds them.
            db.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            return db
        except BaseException:
            db.close()
            raise

    def open(self):
        if self.active or self.collection is not None:
            raise ValueError("layout_read_owner_already_open")
        self.collection = self._connect(self.folder / "collection.sqlite3", rows=True)
        schema = self.collection.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if schema is None or schema[0] != "3":
            raise ValueError("layout_existing_v3_store_required")
        self.active = True
        store = _BorrowedCollection.__new__(_BorrowedCollection)
        store.read_only, store.schema, store.root = True, 3, self.folder
        store.path = self.folder / "collection.sqlite3"
        store.blobs, store.locks = self.folder / "sources", self.folder / "locks"
        store._owner = self
        self.store = store
        cache_path = self.folder / "retrieval.sqlite3"
        if cache_path.exists():
            self.cache = self._connect(cache_path)
        self.metadata["during_read"] = _sidecars(self.folder)
        return self

    def validate(self, state_root=None):
        if (self._attestation is not _ATTESTATION or not self.active or self.thread != threading.get_ident()
                or (state_root is not None and self.root != Path(state_root).absolute())):
            raise ValueError("layout_verified_read_owner_required")
        self.guard.check()
        for db, path in ((self.collection, self.folder / "collection.sqlite3"),
                         (self.cache, self.folder / "retrieval.sqlite3")):
            if db is None:
                continue
            if not db.in_transaction or db.execute("PRAGMA query_only").fetchone()[0] != 1:
                raise ValueError("layout_verified_read_owner_required")
            databases = db.execute("PRAGMA database_list").fetchall()
            main = [r for r in databases if r[1] == "main"]
            if (len(main) != 1 or Path(main[0][2]).absolute() != path
                    or any(r[1] != "main" and (r[1] != "temp" or r[2]) for r in databases)):
                raise ValueError("layout_read_owner_path_mismatch")

    def close(self):
        self.active = False
        for db in (self.cache, self.collection):
            if db is not None:
                db.close()
        self.metadata["after"] = _sidecars(self.folder)
        self.metadata["existence_or_stat_changed"] = (self.metadata["before"] != self.metadata["after"]
            or self.metadata["before"] != self.metadata.get("during_read", self.metadata["before"]))


@contextmanager
def read_transactions(state_root, *, budgets=semantic.Budgets(), cancelled=lambda: False):
    """Caller owns this scope. Neither graph nor affinity closes borrowed reads."""
    budgets.validate()
    guard = semantic._Guard(budgets, cancelled)
    owner = ReadTransactions(state_root, guard)
    try:
        with semantic._sql_errors(guard):
            yield owner.open()
    finally:
        owner.close()


def _query(raw_query):
    if raw_query is not None and not isinstance(raw_query, str):
        raise ValueError("layout_query_invalid")
    if raw_query is not None and (len(raw_query) > MAX_QUERY_BYTES or len(raw_query.encode()) > MAX_QUERY_BYTES):
        raise ValueError("layout_query_byte_budget")
    options = semantic._json(raw_query) if raw_query else {}
    if not isinstance(options, dict) or not set(options) <= QUERY_KEYS:
        raise ValueError("layout_query_invalid")
    if options.get("details", False) is not False:
        raise ValueError("layout_query_view_only")
    if "search" in options and (not isinstance(options["search"], str) or len(options["search"]) > 256):
        raise ValueError("collection_graph_filter_invalid")
    return options


def _empty_coverage():
    return {"requested_nodes": 0, "usable_vector_nodes": 0, "rejected_nodes": 0, "reasons": {},
            "node_coverage": None, "usable_by_project": {}, "cross_project_pairs_excluded": 0,
            "possible_vector_pairs": 0, "eligible_same_project_pairs": 0, "pairs_evaluated": 0,
            "dimension_products": 0, "pairs_above_threshold": 0, "neighbor_union_candidates": 0,
            "candidates_returned": 0, "candidates_truncated": 0, "pair_coverage": None, "pair_budget_exhausted": False}


def _base(projects, refs, revision, options):
    # Minimal current refs only; never body, label, source URL or original ID.
    binding = {"input_nodes": refs, "input_projects": projects, "profile_sha256": PROFILE_SHA256}
    return {"schema": "alden-layout-affinity-v1", "state": "unavailable", "purpose": "layout_evidence_only",
            "input_nodes": refs, "input_projects": projects, "profile": asdict(PROFILE),
            "profile_sha256": PROFILE_SHA256, "nodes": [], "candidates": [], "coverage": _empty_coverage(),
            "binding": {"input_sha256": digest(encoded(binding)), "query_sha256": digest(encoded(options)),
                        "snapshot": "same caller-owned collection read transaction as graph_page; separate cache read snapshot",
                        "graph_revision": revision, "profile_basis": "expected existing E5; each admitted vector checked"},
            "quality_limits": ["cosine .65 is not semantic gold or validated cluster quality",
                               "top-k/hub/ID-order/candidate-budget bias", "layout only; no facts edges or identity merge"]}


def _error(error):
    message = str(error)
    return message if message.startswith(("layout_", "affinity_", "collection_")) and len(message) <= 160 else "layout_read_failed"


def _on_reads(owner, options):
    owner.validate()
    store, guard = owner.store, owner.guard
    known = [p["project"] for p in store.projects()]
    projects = options.get("projects", known)
    if not isinstance(projects, list) or len(projects) > 16 or any(not isinstance(p, str) or p not in known for p in projects):
        raise ValueError("collection_project_scope_invalid")
    projects = sorted(set(projects))
    page = store.graph_page(projects=projects, limit=options.get("limit", 120), offset=options.get("offset", 0),
                            focus=options.get("focus"), hops=options.get("hops", 1), query=options.get("search", ""),
                            target_id=options.get("target_id"), platform=options.get("platform"),
                            node_type=options.get("node_type"), relation=options.get("relation"),
                            since=options.get("since"), until=options.get("until"), overview=options.get("overview", False))
    if page.get("ok") is not True:
        raise ValueError("layout_graph_read_failed")
    refs = [{k: node[k] for k in ("id", "source_version", "source_target")} for node in page["nodes"]]
    if any(ref["id"].startswith("memory:") for ref in refs):
        raise ValueError("layout_non_collection_node")
    if len(refs) > min(MAX_COLLECTION_PAGE, guard.budgets.max_nodes):
        raise ValueError("affinity_node_budget")
    # graph_page order is the authoritative displayed order. Do not reorder
    # input_nodes; the shared evaluator sorts only its deterministic pair work.
    expected = options.get("expected_version")
    if expected is not None:
        if not isinstance(expected, str) or not 1 <= len(expected) <= 256 or not options.get("focus"):
            raise ValueError("collection_graph_filter_invalid")
        focused = next((n for n in refs if n["id"] == options["focus"]), None)
        if focused is None or focused["source_version"] != expected:
            raise ValueError("collection_graph_version_changed")
    revision = page.get("revision", 0)
    result = _base(projects, refs, revision, options)
    result["coverage"].update(requested_nodes=len(refs), rejected_nodes=len(refs))
    try:
        if owner.cache is None:
            raise ValueError("layout_existing_retrieval_required")
        if refs:
            raw = semantic._affinity_on_reads(store, owner.collection, owner.cache, projects, refs,
                                              budgets=guard.budgets, guard=guard)
            result.update(state=raw["state"], coverage=raw["coverage"],
                          nodes=[{k: proof[k] for k in _PROOF_KEYS} for proof in raw["nodes"]],
                          candidates=[{k: candidate[k] for k in ("source", "target", "cosine", "projects")}
                                      for candidate in raw["candidates"]])
            if raw["coverage"]["usable_vector_nodes"] == 0:
                result["state"] = "unavailable"
            result["binding"].update(collection_journal_id=raw["journal_id"],
                                    evaluator_nodes_sha256=raw["input_sha256"],
                                    admitted_provenance_sha256=digest(encoded(result["nodes"])))
        else:
            result["state"] = "bounded_ready"
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        if isinstance(error, sqlite3.Error) and guard.sql_failure is not None:
            error = guard.sql_failure
        result.update(state="unavailable", reason=_error(error), nodes=[], candidates=[])
        result["coverage"]["reasons"] = {_error(error): len(refs)}
    result["coverage"].update(graph_total_nodes=page.get("total_nodes", 0), graph_next=page.get("next"))
    result["metrics"] = {**guard.counts, "wall_seconds": time.monotonic() - guard.started,
                         "cpu_seconds": time.process_time() - guard.cpu_started, "process_peak_rss_bytes": semantic._rss()}
    result["budgets"] = {**asdict(guard.budgets), "query_bytes": MAX_QUERY_BYTES, "response_bytes": MAX_RESPONSE_BYTES}
    return {"ok": True, "layout_affinity": result, "revision": revision}


def read_action(state_root, raw_query, *, transactions=None, budgets=semantic.Budgets(), cancelled=lambda: False):
    """collection-affinity endpoint; server obtains current filtered graph refs.

    A parent may reuse an already-open read_transactions scope using the keyword
    transactions. Plain calls create one scoped owner and return its metadata.
    Invalid queries/graph reads are ok:false. Missing or failed vector reads are
    ok:true with unavailable and no partial candidates. No client node payload.
    """
    owner = None
    try:
        options = _query(raw_query)
        budgets.validate()
        if cancelled():
            raise RuntimeError("affinity_cancelled")
        if transactions is None:
            with read_transactions(state_root, budgets=budgets, cancelled=cancelled) as owner:
                result = _on_reads(owner, options)
        else:
            if type(transactions) is not ReadTransactions:
                raise ValueError("layout_verified_read_owner_required")
            owner = transactions
            owner.validate(state_root)
            if owner.guard.budgets != budgets:
                raise ValueError("affinity_guard_budget_mismatch")
            original_cancelled = owner.guard.cancelled
            owner.guard.cancelled = lambda: original_cancelled() or cancelled()
            try:
                with semantic._sql_errors(owner.guard):
                    result = _on_reads(owner, options)
            finally:
                owner.guard.cancelled = original_cancelled
        result["layout_affinity"]["sqlite_read_metadata"] = owner.metadata
        # Compact serialized contract must stay bounded even for 1984 proofs.
        stop_reason = None
        if len(encoded(result)) > MAX_RESPONSE_BYTES:
            stop_reason = "layout_response_byte_budget"
        if result["layout_affinity"]["state"] != "unavailable":
            try:
                owner.guard.check()
            except RuntimeError as error:
                stop_reason = _error(error)
        if stop_reason is not None:
            unavailable = _base(result["layout_affinity"]["input_projects"],
                                result["layout_affinity"]["input_nodes"], result["revision"], options)
            unavailable["reason"] = stop_reason
            unavailable["coverage"].update(requested_nodes=len(unavailable["input_nodes"]),
                                           rejected_nodes=len(unavailable["input_nodes"]))
            unavailable["sqlite_read_metadata"] = owner.metadata
            result["layout_affinity"] = unavailable
        if len(encoded(result)) > MAX_RESPONSE_BYTES:
            return {"ok": False, "error": "layout_response_byte_budget"}
        return result
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        return {"ok": False, "error": _error(error), "error_type": type(error).__name__}
