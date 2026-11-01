"""The native fixture never seeds, resets or adopts a user's collection."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore
from alden_entity_fixture import PURPOSE, create_fixture, verify_fixture


class EntityFixtureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'resolution-state'
        self.root.mkdir(mode=0o700)
        (self.root / 'owned-entity-audit.json').write_text(json.dumps({'purpose': PURPOSE, 'schema': 1}))

    def test_seeds_once_with_disabled_target_and_verifies_original_tables(self):
        result = create_fixture(self.root); self.assertTrue(result['ok'])
        store = CollectionStore.open_existing(self.root)
        with store.database() as db:
            self.assertEqual(db.execute('SELECT enabled FROM targets').fetchone()[0], 0)
        self.assertEqual(verify_fixture(self.root)['active_groups'], 0)
        with self.assertRaisesRegex(ValueError, 'existing_state_refused'):
            create_fixture(self.root)
        self.assertTrue(verify_fixture(self.root)['original_sources_preserved'])

    def test_mismatched_marker_and_foreign_root_never_create_a_store(self):
        (self.root / 'owned-entity-audit.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'scope_invalid'):
            create_fixture(self.root)
        self.assertFalse((self.root / 'knowledge').exists())
        with self.assertRaisesRegex(ValueError, 'scope_invalid'):
            verify_fixture(self.root.parent)

    def test_modified_originals_are_detected(self):
        create_fixture(self.root)
        with CollectionStore.open_existing(self.root).database() as db:
            # open_existing is read-only; the mutation is explicitly synthetic.
            pass
        import sqlite3
        with sqlite3.connect(self.root / 'knowledge/collection/collection.sqlite3') as db:
            db.execute("UPDATE documents SET availability='deleted' WHERE rowid=1")
        with self.assertRaisesRegex(ValueError, 'original_sources_changed'):
            verify_fixture(self.root)
