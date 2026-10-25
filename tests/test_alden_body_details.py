from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, read_action, encoded, digest
from alden_body_normalization import analyze_body_fields


class BodyDetailTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = CollectionStore(self.root)
        self.target = self.store.register(platform='graph',original_id='source',kind='source',label='자료',projects=['project'])
        self.body = '😀 기한 2026년 2월 23일, 연봉 5천만원, 수량 −5명'
        self.ingest(self.body)
        self.node = self.store.graph_page(projects=['project'])['nodes'][0]

    def ingest(self, body, **extra):
        self.store.ingest(self.target,[{'platform':'graph','original_id':'source-record','label':'자료',
            'text':body,'raw':{'localOriginalText':body},**extra}])

    def details(self, **kwargs):
        return read_action(self.root,'collection-graph',json.dumps({'projects':['project'],
            'focus':self.node['id'],'hops':0,'details':True,'expected_version':self.node['source_version']}),**kwargs)

    def test_joined_time_suffix_reaches_details_without_a_false_canonical_time(self):
        body = '😀 시각 2026-02-23T12:30Z+09:00, 확인 2026-02-23T12:30:00+09:00.'
        self.ingest(body)
        self.node = self.store.graph_page(projects=['project'])['nodes'][0]
        detail = self.details()['details']
        self.assertEqual(detail['body'], body)
        note = detail['body_notation']
        self.assertEqual(note['binding']['body_sha256'], hashlib.sha256(body.encode()).hexdigest())
        times = [item for item in note['analysis']['candidates'] if item['kind'] == 'time']
        self.assertEqual(len(times), 2)
        self.assertEqual((times[0]['status'], times[0]['canonical']), ('unsupported', None))
        self.assertEqual(times[0]['reason'], 'time_suffix_not_fully_parsed')
        self.assertEqual((times[1]['status'], times[1]['canonical']), ('normalized', '2026-02-23T03:30:00Z'))
        for item in times:
            self.assertEqual(body[item['start']:item['end']], item['raw'])
            self.assertEqual(body.encode('utf-16-le')[item['utf16_start'] * 2:item['utf16_end'] * 2].decode('utf-16-le'), item['raw'])

    def test_actual_read_boundary_binds_the_exact_displayed_body_and_source_receipt(self):
        detail = self.details()['details']
        note = detail['body_notation']
        self.assertEqual(note['binding'],{'basis':'verified_source_record_detail','document_id':detail['node_id'],
            'target_id':detail['target_id'],'version':detail['version'],'raw_sha256':detail['raw_sha256'],
            'body_sha256':hashlib.sha256(detail['body'].encode()).hexdigest(),'body_scope':'detail_body'})
        self.assertEqual(note['analysis']['body_sha256'],note['binding']['body_sha256'])
        self.assertEqual(len(note['analysis']['candidates']),3)
        for item in note['analysis']['candidates']:
            self.assertEqual(detail['body'][item['start']:item['end']],item['raw'])

    def test_annotations_cover_the_returned_excerpt_not_hidden_source_tail(self):
        self.ingest('가' * 12000 + '\n수량 8명')
        self.node = self.store.graph_page(projects=['project'])['nodes'][0]
        detail = self.details()['details']
        self.assertTrue(detail['truncated'])
        self.assertEqual(detail['body_notation']['binding']['body_scope'],'detail_body_excerpt')
        self.assertEqual(detail['body_notation']['analysis']['candidates'],[])

    def test_excerpt_cut_cannot_normalize_the_end_of_a_partial_token(self):
        cases = (('2026-02-23T12:30Z+09:00', '2026-02-23'),
                 ('2026-02-23T12:30Z+09:00', '2026-02-23T12:30Z'),
                 ('5mg', '5m'), ('2%p', '2%'), ('10~20ms', '10~20m'),
                 ('1억5000만원', '1억5000만'))
        for full, visible in cases:
            with self.subTest(full=full, visible=visible):
                prefix = '😀 수량 3kg ' + 'x' * (12000 - len(visible) - len('😀 수량 3kg ') - 1) + ' '
                self.ingest(prefix + full)
                self.node = self.store.graph_page(projects=['project'])['nodes'][0]
                detail = self.details()['details']
                self.assertEqual(detail['body'], prefix + visible)
                self.assertTrue(detail['truncated'])
                note = detail['body_notation']
                self.assertEqual(note['binding']['body_scope'], 'detail_body_excerpt')
                self.assertEqual(note['binding']['body_sha256'], hashlib.sha256(detail['body'].encode()).hexdigest())
                analysis = note['analysis']
                self.assertEqual([item['raw'] for item in analysis['candidates']], ['3kg'])
                self.assertTrue(analysis['truncated'])
                self.assertEqual(analysis['state'], 'bounded_candidates')

    def test_complete_body_and_visible_delimiter_preserve_terminal_notation(self):
        for literal in ('2026-02-23T12:30Z', '5mg', '2%p', '10~20ms', '1억5000만원'):
            for excerpt in (False, True):
                with self.subTest(literal=literal, excerpt=excerpt):
                    visible = literal + ('.' if excerpt else '')
                    prefix = '😀 수량 3kg ' + 'x' * (12000 - len(visible) - len('😀 수량 3kg ') - 1) + ' '
                    body = prefix + visible + (' 뒤의 본문' if excerpt else '')
                    self.ingest(body)
                    self.node = self.store.graph_page(projects=['project'])['nodes'][0]
                    detail = self.details()['details']
                    self.assertEqual(detail['truncated'], excerpt)
                    analysis = detail['body_notation']['analysis']
                    self.assertEqual([item['raw'] for item in analysis['candidates']], ['3kg', literal])
                    self.assertTrue(all(item['status'] == 'normalized' for item in analysis['candidates']))
                    self.assertFalse(analysis['truncated'])

    def test_metadata_only_records_are_not_annotated_as_body_content(self):
        self.ingest('본문을 획득하지 않은 파일 목록 50GB',content_indexed=False)
        self.node = self.store.graph_page(projects=['project'])['nodes'][0]
        self.assertIsNone(self.details()['details']['body_notation'])

    def test_committed_version_change_during_analysis_rejects_old_packet(self):
        def changed(body):
            result = analyze_body_fields(body)
            self.ingest('개정된 수량은 7명입니다.')
            return result
        with patch('alden_body_normalization.analyze_body_fields',side_effect=changed):
            with self.assertRaisesRegex(RuntimeError,'collection_graph_version_changed'):
                self.details()

    def test_permission_revocation_during_analysis_rejects_old_packet(self):
        def revoked(body):
            result = analyze_body_fields(body)
            with self.store.database() as db:
                db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?",(self.target,))
            return result
        with patch('alden_body_normalization.analyze_body_fields',side_effect=revoked):
            with self.assertRaisesRegex(RuntimeError,'collection_source_authorization_changed'):
                self.details()

    def protected_source(self):
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?",(self.target,))
        self.target = self.store.register(platform='telegram',original_id='protected',kind='local-db',
            label='보호된 자료',projects=['project'])
        self.store.source_access = lambda *args: True
        self.ingest(self.body)
        self.node = self.store.graph_page(projects=['project'])['nodes'][0]

    def test_native_source_grant_revocation_during_analysis_rejects_the_packet(self):
        self.protected_source()
        allowed = True
        def revoked(body):
            nonlocal allowed
            result = analyze_body_fields(body)
            allowed = False
            return result
        with patch('alden_body_normalization.analyze_body_fields',side_effect=revoked):
            with self.assertRaisesRegex(RuntimeError,'collection_source_authorization_changed'):
                self.details(source_access=lambda *args: allowed,source_revision=lambda *args:'a'*64)

    def test_native_source_revision_change_during_analysis_rejects_the_packet(self):
        self.protected_source()
        revision = 'a'*64
        def changed(body):
            nonlocal revision
            result = analyze_body_fields(body)
            revision = 'b'*64
            return result
        with patch('alden_body_normalization.analyze_body_fields',side_effect=changed):
            with self.assertRaisesRegex(RuntimeError,'collection_source_authorization_changed'):
                self.details(source_access=lambda *args:True,source_revision=lambda *args:revision)

    def test_changed_stored_body_without_new_version_is_an_integrity_error(self):
        def corrupted(body):
            result = analyze_body_fields(body)
            with self.store.database() as db:
                db.execute('UPDATE versions SET body=? WHERE id=?',('forged 99명',self.node['source_version']))
            return result
        with patch('alden_body_normalization.analyze_body_fields',side_effect=corrupted):
            with self.assertRaisesRegex(RuntimeError,'collection_source_integrity'):
                self.details()

    def test_raw_path_swap_cannot_borrow_another_valid_archive(self):
        def swapped(body):
            result = analyze_body_fields(body)
            raw = encoded({'localOriginalText':'unrelated 99명'})
            name = digest(raw) + '.json'
            (self.store.blobs / name).write_bytes(raw)
            with self.store.database() as db:
                db.execute('UPDATE versions SET raw_path=? WHERE id=?',(name,self.node['source_version']))
            return result
        with patch('alden_body_normalization.analyze_body_fields',side_effect=swapped):
            with self.assertRaisesRegex(RuntimeError,'collection_source_integrity'):
                self.details()

    def test_final_read_rechecks_after_entity_projection(self):
        def project(payload,*args):
            self.ingest('다른 버전 9명')
            return payload
        with patch('alden_entity_projection.project_groups',side_effect=project):
            with self.assertRaisesRegex(RuntimeError,'collection_graph_version_changed'):
                self.details()

    def test_mismatched_parser_body_digest_is_not_exposed(self):
        result = analyze_body_fields(self.body)
        result['body_sha256'] = '0' * 64
        with patch('alden_body_normalization.analyze_body_fields',return_value=result):
            with self.assertRaisesRegex(RuntimeError,'collection_body_notation_binding_invalid'):
                self.details()

    def test_schema_two_can_still_read_without_new_annotations(self):
        with self.store.database() as db:
            db.execute('ALTER TABLE memberships DROP COLUMN availability')
            db.execute("UPDATE meta SET value='2' WHERE key='schema'")
        detail = self.details()['details']
        self.assertEqual(detail['body'],self.body)
        self.assertIsNone(detail['body_notation'])


if __name__ == '__main__':
    unittest.main()
