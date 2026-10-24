"""One-time, app-owned synthetic collection for native review workflow QA."""
from pathlib import Path
import os
import stat

from alden_collection import CollectionStore, digest, encoded
from alden_graph_quality import _json

PURPOSE = 'alden-owned-entity-resolution-audit-v1'
ORIGINAL_TABLES = ('meta', 'targets', 'target_projects', 'documents', 'versions',
                   'memberships', 'relations', 'relation_versions', 'runs', 'events',
                   'target_snapshots', 'event_counts')


def original_digest(store):
    with store.database() as db:
        tables = {name: [dict(row) for row in db.execute('SELECT * FROM ' + name + ' ORDER BY rowid')]
                  for name in ORIGINAL_TABLES}
    return digest(encoded(tables))


def _owned_root(root):
    root = Path(root)
    if (not root.is_absolute() or root.name != 'resolution-state'
            or any(p.is_symlink() for p in (root, *root.parents))):
        raise ValueError('entity_fixture_scope_invalid')
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o777 != 0o700:
        raise ValueError('entity_fixture_scope_invalid')
    marker = root / 'owned-entity-audit.json'
    if marker.is_symlink() or marker.stat().st_size > 4096:
        raise ValueError('entity_fixture_scope_invalid')
    declaration = _json(marker.read_bytes())
    if declaration != {'purpose': PURPOSE, 'schema': 1}:
        raise ValueError('entity_fixture_scope_invalid')
    return root


def create_fixture(root):
    root = _owned_root(root)
    if (root / 'knowledge').exists() or (root / 'knowledge').is_symlink():
        raise ValueError('entity_fixture_existing_state_refused')
    store = CollectionStore(root)
    target = store.register(platform='graph', original_id='owned-entity-audit',
                            kind='source', label='Owned synthetic review fixture', projects=['audit'])
    records = [{'platform': 'graph', 'original_id': name, 'label': name,
                'kind': 'source', 'content_indexed': 1,
                'text': 'Synthetic same-source content for review workflow.' if name in ('a', 'b')
                        else 'Synthetic distinct source content.'} for name in ('a', 'b', 'c')]
    relations = [{'source_platform': 'graph', 'source_id': name, 'target_platform': 'graph',
                  'target_id': 'c', 'type': 'mentions', 'evidence': {'scope': 'synthetic owned audit'}}
                 for name in ('a', 'b')]
    store.ingest(target, records, relations=relations)
    with store.database() as db:
        db.execute('UPDATE targets SET enabled=0 WHERE id=?', (target,))
    receipt = {'schema': 1, 'purpose': PURPOSE, 'project': 'audit',
               'source_digest': original_digest(store), 'target_id': target}
    path = root / 'entity-fixture-receipt.json'
    with path.open('x') as handle:
        handle.write(encoded(receipt).decode())
    path.chmod(0o600)
    return {'ok': True, **receipt}


def verify_fixture(root):
    root = _owned_root(root)
    receipt = _json((root / 'entity-fixture-receipt.json').read_bytes())
    store = CollectionStore.open_existing(root)
    if (store is None or receipt.get('purpose') != PURPOSE
            or original_digest(store) != receipt['source_digest']):
        raise ValueError('entity_fixture_original_sources_changed')
    with store.database() as db:
        feature = db.execute("SELECT 1 FROM sqlite_master WHERE name='resolution_active_groups'").fetchone()
        active = db.execute('SELECT COUNT(*) FROM resolution_active_groups').fetchone()[0] if feature else 0
        events = db.execute('SELECT COUNT(*) FROM resolution_events').fetchone()[0] if feature else 0
    return {'ok': True, 'original_sources_preserved': True, 'active_groups': active,
            'resolution_events': events, 'source_digest': receipt['source_digest']}
