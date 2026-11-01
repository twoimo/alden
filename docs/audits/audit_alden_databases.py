"""Bounded, read-only inventory of Alden's owned SQLite stores.

No private text, document IDs, vectors, credentials or DB copies in the report.
Timings are local diagnostic samples, not a matched database benchmark.
"""
import argparse
from contextlib import closing
import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import time

STORES = {
    'collection': ('knowledge/collection/collection.sqlite3', ['targets', 'documents', 'versions', 'relations', 'relation_versions', 'events', 'runs']),
    'retrieval': ('knowledge/collection/retrieval.sqlite3', ['vectors', 'texts', 'text_fts']),
    'scheduler': ('knowledge/collection/scheduler.sqlite3', ['cycles', 'attempts', 'manual_requests']),
    'realtime': ('knowledge/collection/realtime.sqlite3', ['policy', 'inputs', 'heartbeat']),
    'legacy_graph': ('knowledge-graph.sqlite3', ['kg_entities', 'kg_relations']),
    'legacy_dense': ('knowledge-dense-ann.sqlite3', ['dense_vectors', 'ann_buckets']),
}


def quoted(value):
    return '"' + value.replace('"', '""') + '"'


def audit(path, allowed):
    if any(p.is_symlink() for p in [path, *path.parents]): return {'state': 'unsafe_path'}
    if not path.is_file(): return {'state': 'not_present'}
    result = {'state': 'observed', 'bytes': path.stat().st_size,
              'wal_bytes': Path(str(path) + '-wal').stat().st_size if Path(str(path) + '-wal').is_file() else 0}
    with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True, timeout=.15)) as db:
        db.execute('PRAGMA query_only=ON')
        result['journal_mode'] = db.execute('PRAGMA journal_mode').fetchone()[0]
        result['pages'] = db.execute('PRAGMA page_count').fetchone()[0]
        result['page_size'] = db.execute('PRAGMA page_size').fetchone()[0]
        result['free_pages'] = db.execute('PRAGMA freelist_count').fetchone()[0]
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result['tables'] = {}
        for table in allowed:
            if table not in tables: continue
            began = time.monotonic()
            db.set_progress_handler(lambda: int(time.monotonic() - began > 3), 10000)
            try:
                count = db.execute('SELECT COUNT(*) FROM ' + quoted(table)).fetchone()[0]
                indexes = []
                for index in db.execute('PRAGMA index_list(' + quoted(table) + ')').fetchall():
                    indexes.append({'name': index[1], 'unique': bool(index[2]), 'partial': bool(index[4]),
                        'columns': [r[2] for r in db.execute('PRAGMA index_info(' + quoted(index[1]) + ')')]})
                result['tables'][table] = {'rows': count, 'count_ms': (time.monotonic() - began) * 1000, 'indexes': indexes}
            except sqlite3.Error:
                result['tables'][table] = {'state': 'query_budget_or_error'}
            finally: db.set_progress_handler(None, 0)
        if 'vectors' in tables:
            sql = 'SELECT document_id,version,text_hash,model,endpoint,encoding FROM vectors'
            result['vector_metadata_scan_plan'] = [r[3] for r in db.execute('EXPLAIN QUERY PLAN ' + sql)]
            samples = []
            for _ in range(5):
                began = time.monotonic()
                db.set_progress_handler(lambda: int(time.monotonic() - began > 3), 10000)
                try:
                    count = sum(1 for _ in db.execute(sql))
                    samples.append((time.monotonic() - began) * 1000)
                except sqlite3.Error:
                    result['vector_metadata_scan_state'] = 'query_budget_or_error'; break
                finally: db.set_progress_handler(None, 0)
            if samples:
                result['vector_metadata_scan'] = {'rows': count, 'samples_ms': samples, 'median_ms': statistics.median(samples),
                    'scope': 'Existing indexer metadata SELECT only; excludes retained-source checks, text projection, model calls, vector math and writes.'}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): parser.error('output already exists')
    result = {'schema': 1, 'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'sqlite_runtime': sqlite3.sqlite_version, 'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'scope': 'Read-only current stores; aggregate schema/counts/metadata-scan timings only. No source migration or DB-engine winner inferred.',
              'stores': {name: audit(args.state_root / relative, tables) for name, (relative, tables) in STORES.items()}}
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with args.output.open('x') as handle: json.dump(result, handle, ensure_ascii=False, indent=2); handle.write('\n')
    print(json.dumps({name: {'state': row['state'], 'bytes': row.get('bytes'),
        'rows': {table: info.get('rows') for table, info in row.get('tables', {}).items()}}
        for name, row in result['stores'].items()}, ensure_ascii=False))


if __name__ == '__main__': main()
