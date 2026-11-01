"""Provider title normalization and typed source proofs, using synthetic data only."""
from contextlib import contextmanager, nullcontext
import copy
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch

from tests import test_alden_gbrain_import as fixture
import alden_collection as collection
from alden_collection import identity
import alden_collection_export as archive
from alden_gbrain_client import GbrainError
import alden_gbrain_import as full


# ECMAScript String.trim whitespace, including BOM (which Python strip retains).
# Controls in this set are not used as source labels: FullProjection rejects them.
JS_TRIM_CHARACTERS = (
    '\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680'
    '\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a'
    '\u2028\u2029\u202f\u205f\u3000\ufeff'
)


def provider_trim(title):
    return title.strip(JS_TRIM_CHARACTERS)


class ProviderMetadataEngine(fixture.FakeEngine):
    """Only the existing get API changes; no paging or other API is invented."""
    def __init__(self, *, title_change=None, proof_change=None):
        super().__init__()
        self.title_change = title_change
        self.proof_change = proof_change
        self.changed_focus = False

    def get(self, slug):
        page = super().get(slug)
        page['title'] = provider_trim(page['title'])
        if page['frontmatter']['alden_node_id'] == identity('graph', 'a'):
            if self.title_change is not None:
                page['title'] = self.title_change(page['title'])
                self.changed_focus = True
            if self.proof_change is not None:
                key, value = self.proof_change
                page['frontmatter'][key] = value
                self.changed_focus = True
        return page


class ActualMetadataTests(unittest.TestCase):
    @contextmanager
    def synthetic_source(self, **engine_options):
        with patch('subprocess.Popen', side_effect=AssertionError('synthetic engines only')), \
                patch.object(fixture, 'FakeEngine', side_effect=lambda: ProviderMetadataEngine(**engine_options)):
            f = fixture.FullImportTests()
            try:
                f.setUp()
                yield f
            finally:
                f.doCleanups()

    @contextmanager
    def original_export_row(self, f, label):
        # Ingestion and overview otherwise remove these spaces before the
        # importer sees them. Preserve only this exact synthetic source label,
        # before ingestion hashes/versioning and before actual export hashing.
        normalize = collection.normalized_text
        display = archive.overview_display_label

        def source_text(value):
            return value if value == label else normalize(value)

        def source_label(value, body=None):
            return value if value == label else display(value, body)

        with patch.object(collection, 'normalized_text', side_effect=source_text), \
                patch.object(archive, 'overview_display_label', side_effect=source_label):
            f.store.ingest(f.target, [{'original_id': 'a', 'label': label,
                                       'text': 'PUBLIC_SYNTHETIC_BODY'}])
            output = f.base / 'expected-source-archive'
            receipt = archive.export_graph(f.root, ['one'], output)
            with sqlite3.connect((output / 'graph.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
                payload = db.execute('SELECT payload FROM nodes WHERE id=?', (identity('graph', 'a'),)).fetchone()[0]
            node = json.loads(payload)
            self.assertEqual(node['label'], label)
            self.assertEqual(archive.verify_current_export(f.root, ['one'], output), receipt)
            yield node

    def padded_public_label(self, padding):
        core = 'Public  synthetic source label '
        return padding + core.ljust(80 - 2 * len(padding), 'x') + padding

    def assert_trimmed_import_preserves_source(self, padding, incremental):
        label = self.padded_public_label(padding)
        self.assertEqual(len(label), 80)
        self.assertNotEqual(provider_trim(label), label)
        self.assertIn('Public  synthetic', provider_trim(label))
        with self.synthetic_source() as f, self.original_export_row(f, label) as expected:
            source_before = f.source_bytes()
            accepted = f.cache.refresh(incremental=incremental)
            self.assertEqual(accepted['state'], 'published')
            self.assertEqual((accepted['pages'], accepted['verified_queries']), (4, 4))
            projection = f.projection()
            page = next(p for p in projection.pages() if p['id'] == expected['id'])
            self.assertEqual(json.loads(page['payload']), expected)
            self.assertEqual(expected['source_contexts'][0]['label'], label)
            engine = f.engines[projection.root]
            readback = engine.get(page['slug'])
            self.assertEqual(readback['title'], provider_trim(label))
            proof = readback['frontmatter']
            expected_hash = hashlib.sha256(json.dumps(
                expected, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            self.assertEqual(proof['alden_record_sha256'], expected_hash)
            self.assertEqual(proof['alden_node_id'], expected['id'])
            self.assertEqual(proof['alden_source_target'], expected['source_target'])
            self.assertEqual(proof['alden_source_version'], expected['source_version'])
            result = f.query('a')
            self.assertTrue(result['ok'])
            self.assertEqual(next(n for n in result['nodes'] if n['id'] == expected['id']), expected)
            self.assertEqual(source_before, f.source_bytes())

    def test_trimmed_ascii_title_preserves_original_label_payload_and_proof(self):
        for incremental in (False, True):
            with self.subTest(incremental=incremental):
                self.assert_trimmed_import_preserves_source('  ', incremental)

    def test_trimmed_unicode_title_preserves_original_label_payload_and_proof(self):
        for name, padding in (('nbsp', '\u00a0'), ('em-space', '\u2003'),
                              ('narrow-nbsp', '\u202f'), ('ideographic-space', '\u3000'), ('bom', '\ufeff')):
            for incremental in (False, True):
                with self.subTest(space=name, incremental=incremental):
                    self.assert_trimmed_import_preserves_source(padding, incremental)

    def assert_changed_metadata_is_rejected(self, incremental, **engine_options):
        # Plain controls isolate existing rejection from the baseline trim bug;
        # padded cases guard the relaxed title path after the product is fixed.
        for padded in (False, True):
            with self.subTest(padded=padded), self.synthetic_source(**engine_options) as f:
                source = self.original_export_row(f, self.padded_public_label('  ')) if padded else nullcontext()
                with source:
                    source_before = f.source_bytes()
                    with self.assertRaisesRegex(GbrainError, '^gbrain_cache_readback_invalid$'):
                        f.cache.refresh(incremental=incremental)
                    self.assertEqual(len(f.engines), 1)
                    self.assertTrue(next(iter(f.engines.values())).changed_focus)
                    self.assertIsNone(f.cache.active())
                    self.assertEqual(list(f.cache.generations.iterdir()), [])
                    self.assertEqual(source_before, f.source_bytes())

    def test_internal_title_character_change_is_rejected(self):
        for incremental in (False, True):
            with self.subTest(incremental=incremental):
                self.assert_changed_metadata_is_rejected(
                    incremental, title_change=lambda title: title.replace('i', 'X', 1))

    def test_internal_ascii_or_unicode_title_space_changes_are_rejected(self):
        for replacement in ('', '  ', '\u00a0'):
            for incremental in (False, True):
                with self.subTest(replacement=repr(replacement), incremental=incremental):
                    self.assert_changed_metadata_is_rejected(
                        incremental, title_change=lambda title: title.replace(' ', replacement))

    def test_record_proof_changes_are_rejected_after_provider_title_normalization(self):
        for key, value in (('alden_record_sha256', '0' * 64),
                           ('alden_source_version', 'different-source-version'),
                           ('alden_source_target', 'different-source-target')):
            for incremental in (False, True):
                with self.subTest(field=key, incremental=incremental):
                    self.assert_changed_metadata_is_rejected(incremental, proof_change=(key, value))

    def assert_context_type_change_is_rejected(self, field, replacement):
        for incremental in (False, True):
            with self.subTest(incremental=incremental), self.synthetic_source() as f:
                f.cache.refresh(incremental=incremental)
                projection = f.projection()
                focus = identity('graph', 'b')
                rows = f.engines[projection.root].graph(projection.slug(focus))
                original = projection.bind(rows, focus=focus)
                self.assertTrue(original['ok'])
                self.assertEqual(len(original['edges']), 3)
                changed = copy.deepcopy(rows)
                observed = next(row for row in changed
                                if json.loads(row['context'])['edge'].get('relation') == 'reference')
                context = json.loads(observed['context'])
                parent, key = field
                before = context['edge'][parent][key]
                if key == 'count':
                    self.assertIs(type(before), int)
                    self.assertEqual(before, 1)
                else:
                    self.assertIs(before, False)
                self.assertEqual(before, replacement)  # Python equality is insufficient for JSON proof types.
                self.assertIsNot(type(before), type(replacement))
                context['edge'][parent][key] = replacement
                observed['context'] = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                self.assertNotEqual(observed['context'], next(row['context'] for row in rows
                                    if row['from_slug'] == observed['from_slug']
                                    and row['to_slug'] == observed['to_slug']
                                    and row['link_type'] == observed['link_type']))
                with self.assertRaisesRegex(GbrainError, '^gbrain_full_result_unbound$'):
                    projection.bind(changed, focus=focus)

    def test_bind_rejects_reference_count_changed_from_integer_to_boolean(self):
        self.assert_context_type_change_is_rejected(('reference_group', 'count'), True)

    def test_bind_rejects_retracted_changed_from_boolean_to_numeric_zero(self):
        self.assert_context_type_change_is_rejected(('evidence', 'retracted'), 0)


if __name__ == '__main__':
    unittest.main()
