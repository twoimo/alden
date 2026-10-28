import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import alden_file_set as files
from alden_collect import collect_target, capture_source
from alden_collection import CollectionStore, encoded, identity
from alden_realtime_collection import RealtimeCollection


class FileSetCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('ALDEN_TEST_TMPDIR'))
        self.root = Path(self.temp.name).resolve()
        self.inputs = self.root / 'originals'; self.inputs.mkdir()
        self.manifest = self.root / 'manifest.json'
        self.state = self.root / 'state'
        self.store = CollectionStore(self.state)
        self.input('a.txt', '가격은 1,200원\n    원문 들여쓰기\n시점: 2026-10-28\n')
        self.rows = [{'id': 'one', 'path': 'a.txt'}]
        self.save_manifest()
        self.target = self.store.register(platform='files', original_id='declared-documents', kind='file-set',
            label='Declared originals', projects=['one'], config={'adapter':'file-set','root':str(self.inputs),'path':str(self.manifest)})

    def tearDown(self):
        self.temp.cleanup()

    def input(self, name, content):
        path = self.inputs / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return path

    def save_manifest(self):
        self.manifest.write_bytes(encoded({'schema':1,'files':self.rows}))

    def versions(self):
        with self.store.database() as db:
            return [tuple(r) for r in db.execute('SELECT id,document_id,raw_sha256,label,body FROM versions ORDER BY id')]

    def bodies(self, project='one'):
        return {r['body'] for r in self.store.search('가격', projects=[project])}

    def test_real_collector_and_retained_capture_include_exact_original_bytes_and_locations(self):
        before = (self.inputs/'a.txt').read_bytes()
        result = collect_target(self.store,self.target)
        self.assertEqual(result['batches'][0]['added'],1)
        self.assertTrue(result['source']['body_complete'])
        self.assertEqual(self.bodies(),{before.decode().rstrip('\n')})
        captured = capture_source(self.store,self.target)
        self.assertEqual(captured['state'], 'complete')
        cursor=json.loads(self.store.target(self.target)['cursor'])
        folder=self.store.root/'source-captures'
        manifest=json.loads((folder/cursor['source_capture']['manifest']).read_bytes())
        packet=json.loads((folder/manifest['source_file']).read_bytes())
        digest=hashlib.sha256(before).hexdigest()
        self.assertEqual(base64.b64decode(packet['blobs_base64'][digest]),before)
        self.assertEqual(packet['files'][0]['record']['input_sha256'],digest)
        self.assertEqual(manifest['original_files'][0]['sha256'],digest)
        self.assertEqual(manifest['records'][0]['json_pointer'],'/files/0/record')
        self.assertTrue(packet['files'][0]['record']['parsed']['segments'])
        self.assertEqual((self.inputs/'a.txt').read_bytes(),before)

    def test_replay_keeps_versions_and_original_capture_identity(self):
        collect_target(self.store,self.target);capture_source(self.store,self.target)
        versions=self.versions();prior=json.loads(self.store.target(self.target)['cursor'])['source_capture']
        result=collect_target(self.store,self.target)
        self.assertEqual(result['batches'][0]['state'],'unchanged')
        self.assertEqual(capture_source(self.store,self.target)['state'],'unchanged')
        self.assertEqual(self.versions(),versions)
        self.assertEqual(json.loads(self.store.target(self.target)['cursor'])['source_capture'],prior)

    def test_equal_byte_copies_keep_two_ids_and_one_blob_then_revision_changes_only_one(self):
        self.input('copy.txt',(self.inputs/'a.txt').read_bytes())
        self.rows.append({'id':'copy','path':'copy.txt'});self.save_manifest()
        first=collect_target(self.store,self.target)
        self.assertEqual(first['batches'][0]['added'],2)
        self.assertEqual(first['source']['file_count'],2)
        self.assertEqual(first['source']['unique_byte_blobs'],1)
        self.assertEqual(len(self.versions()),2)
        self.input('a.txt','가격 개정 1,300원\n')
        second=collect_target(self.store,self.target)
        self.assertEqual(second['batches'][0]['revised'],1)
        self.assertEqual(second['batches'][0]['unchanged'],1)
        self.assertEqual(len(self.versions()),3)

    def test_missing_original_does_not_retract_or_advance_checkpoint(self):
        collect_target(self.store,self.target);prior=self.store.target(self.target)['cursor'];versions=self.versions()
        (self.inputs/'a.txt').unlink()
        with self.assertRaises(FileNotFoundError):collect_target(self.store,self.target)
        self.assertEqual(self.store.target(self.target)['cursor'],prior)
        self.assertEqual(self.versions(),versions)
        self.assertTrue(self.bodies())

    def test_explicit_manifest_removal_retracts_membership_but_preserves_versions(self):
        collect_target(self.store,self.target);versions=self.versions()
        self.rows=[];self.save_manifest()
        result=collect_target(self.store,self.target)
        self.assertEqual(result['batches'][0]['removed'],1)
        self.assertFalse(self.bodies())
        self.assertEqual(self.versions(),versions)
        capture_source(self.store,self.target)

    def test_wrong_project_scope_and_denied_target_do_not_leak_bodies(self):
        collect_target(self.store,self.target)
        self.assertFalse(self.bodies('other'))
        self.store.register(platform='files',original_id='declared-documents',kind='file-set',label='Denied',
            projects=['one'],permission='denied',config={'adapter':'file-set','root':str(self.inputs),'path':str(self.manifest)})
        with patch.object(files,'parse_file',side_effect=AssertionError('body accessed')):
            with self.assertRaisesRegex(ValueError,'scope_denied'):collect_target(self.store,self.target)
        self.assertFalse(self.bodies())

    def test_paused_target_does_not_open_manifest_or_body(self):
        self.store.register(platform='files',original_id='declared-documents',kind='file-set',label='Paused',
            projects=['one'],enabled=False,config={'adapter':'file-set','root':str(self.inputs),'path':str(self.manifest)})
        with patch.object(files,'_manifest',side_effect=AssertionError('input accessed')):
            with self.assertRaisesRegex(RuntimeError,'paused'):collect_target(self.store,self.target)

    def test_file_change_between_parse_and_publication_rolls_back(self):
        collect_target(self.store,self.target);prior=self.store.target(self.target)['cursor'];versions=self.versions()
        self.input('a.txt','가격 개정\n')
        ingest=self.store.ingest
        def race(*args,**kwargs):
            self.input('a.txt','가격 더 늦은 변경\n');return ingest(*args,**kwargs)
        with patch.object(self.store,'ingest',side_effect=race):
            with self.assertRaisesRegex(RuntimeError,'source_changed'):collect_target(self.store,self.target)
        self.assertEqual(self.store.target(self.target)['cursor'],prior)
        self.assertEqual(self.versions(),versions)

    def test_capture_rejects_new_original_bytes_for_old_committed_version(self):
        collect_target(self.store,self.target);prior=self.store.target(self.target)['cursor']
        self.input('a.txt','가격 달라짐\n')
        with self.assertRaisesRegex(RuntimeError,'revision_changed'):capture_source(self.store,self.target)
        self.assertEqual(self.store.target(self.target)['cursor'],prior)

    def test_current_target_binding_cannot_be_replaced_between_parse_and_commit(self):
        collect_target(self.store,self.target);prior=self.store.target(self.target)['cursor'];versions=self.versions()
        self.input('a.txt','가격 개정\n')
        ingest=self.store.ingest
        def race(*args,**kwargs):
            self.store.register(platform='files',original_id='declared-documents',kind='file-set',label='Retargeted',
                projects=['other'],config={'adapter':'file-set','root':str(self.inputs),'path':str(self.manifest)})
            return ingest(*args,**kwargs)
        with patch.object(self.store,'ingest',side_effect=race):
            with self.assertRaisesRegex(RuntimeError,'target_changed'):collect_target(self.store,self.target)
        self.assertEqual(self.store.target(self.target)['cursor'],prior)
        self.assertEqual(self.versions(),versions)

    def test_existing_publication_guard_is_retained_and_can_veto_commit(self):
        collect_target(self.store,self.target);prior=self.store.target(self.target)['cursor'];versions=self.versions()
        self.input('a.txt','가격 개정\n');calls=[]
        @contextmanager
        def deny():
            calls.append('entered')
            raise RuntimeError('owner_commit_veto')
            yield
        with self.assertRaisesRegex(RuntimeError,'owner_commit_veto'):
            collect_target(self.store,self.target,publication_guard=deny)
        self.assertEqual(calls,['entered'])
        self.assertEqual(self.store.target(self.target)['cursor'],prior)
        self.assertEqual(self.versions(),versions)

    def test_realtime_signature_tracks_body_changes_without_manifest_change(self):
        target=self.store.target(self.target);raw=self.manifest.read_bytes()
        first=RealtimeCollection.signature(target)
        self.assertEqual(RealtimeCollection.signature(target),first)
        self.input('a.txt','가격 변동\n')
        self.assertNotEqual(RealtimeCollection.signature(target),first)
        self.assertEqual(self.manifest.read_bytes(),raw)

    def test_manifest_traversal_absolute_duplicate_and_noncanonical_paths_rejected(self):
        variants=[[{'id':'a','path':v}] for v in ['../outside.txt','/outside.txt','sub/../a.txt','./a.txt','x//a.txt','x\\a.txt']]
        variants += [[{'id':'a','path':'a.txt'},{'id':'a','path':'b.txt'}],
                     [{'id':'a','path':'a.txt'},{'id':'b','path':'a.txt'}]]
        for rows in variants:
            with self.subTest(rows=rows):
                self.rows=rows;self.save_manifest()
                with self.assertRaisesRegex(ValueError,'manifest_invalid'):collect_target(self.store,self.target)
        self.assertEqual(self.versions(),[])

    def test_duplicate_json_keys_and_unknown_fields_rejected(self):
        for raw in [b'{"schema":1,"schema":1,"files":[]}',b'{"schema":true,"files":[]}',
                    b'{"schema":1,"files":[],"root":"/"}',b'{"schema":NaN,"files":[]}']:
            with self.subTest(raw=raw):
                self.manifest.write_bytes(raw)
                with self.assertRaisesRegex(ValueError,'manifest_invalid'):collect_target(self.store,self.target)

    def test_symlink_and_nonregular_sources_rejected_without_fallback(self):
        outside=self.root/'outside.txt';outside.write_text('not permitted')
        (self.inputs/'a.txt').unlink();(self.inputs/'a.txt').symlink_to(outside)
        with self.assertRaises((OSError,ValueError)):collect_target(self.store,self.target)
        (self.inputs/'a.txt').unlink();os.mkfifo(self.inputs/'a.txt')
        with self.assertRaisesRegex(ValueError,'nonregular'):collect_target(self.store,self.target)
        self.assertEqual(self.versions(),[])

    def test_source_and_target_byte_budgets_fail_before_publication(self):
        with patch.object(files,'MAX_FILE_BYTES',1):
            with self.assertRaisesRegex(ValueError,'byte_budget'):collect_target(self.store,self.target)
        with patch.object(files,'MAX_TOTAL_FILE_BYTES',1):
            with self.assertRaisesRegex(ValueError,'byte_budget'):collect_target(self.store,self.target)
        self.assertEqual(self.versions(),[])

    def test_unsupported_source_is_not_a_successful_empty_collection(self):
        self.input('unknown.bin',b'not a supported document');self.rows=[{'id':'one','path':'unknown.bin'}];self.save_manifest()
        with self.assertRaisesRegex(ValueError,'document_format_unsupported'):collect_target(self.store,self.target)
        self.assertEqual(self.versions(),[])

    def test_cancellation_prevents_parsing_and_state_publication(self):
        with patch.object(files,'parse_file',side_effect=AssertionError('parser invoked')):
            with self.assertRaisesRegex(RuntimeError,'cancelled'):collect_target(self.store,self.target,cancelled=lambda:True)
        self.assertEqual(self.versions(),[])

    def test_partial_parser_scope_is_retained_and_not_labeled_complete_body(self):
        def partial(data,filename,**kwargs):
            text='가격 발췌';sha=hashlib.sha256(text.encode()).hexdigest()
            return {'processing_version':'document-segments-v1','parser':'controlled-test','text':text,'text_sha256':sha,
                'truncated':True,'scope':{'test':'bounded partial','body_complete':False},'segments':[{'location':{'line':1},'text':text,'text_sha256':sha}]}
        with patch.object(files,'parse_file',side_effect=partial):
            result=collect_target(self.store,self.target)
        self.assertFalse(result['source']['body_complete'])
        self.assertTrue(result['source']['manifest_complete'])


    def test_blank_structural_positions_survive_actual_child_and_collection(self):
        document = io.BytesIO()
        with zipfile.ZipFile(document, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/><w:p><w:r><w:t>visible</w:t></w:r></w:p></w:body></w:document>')
        cases = [('blank.txt', b'first\n\nsecond', 1),
                 ('blank.csv', b'first\n\nsecond', 1),
                 ('blank.docx', document.getvalue(), 0)]
        for name, content, position in cases:
            with self.subTest(filename=name):
                self.input(name, content)
                self.rows = [{'id': 'one', 'path': name}]; self.save_manifest()
                result = collect_target(self.store, self.target)
                self.assertEqual(result['records'], 1)
                capture_source(self.store, self.target)
                cursor = json.loads(self.store.target(self.target)['cursor'])
                folder = self.store.root / 'source-captures'
                manifest = json.loads((folder / cursor['source_capture']['manifest']).read_bytes())
                packet = json.loads((folder / manifest['source_file']).read_bytes())
                segment = packet['files'][0]['record']['parsed']['segments'][position]
                self.assertEqual(segment['text'], '')
                self.assertEqual(segment['text_sha256'], hashlib.sha256(b'').hexdigest())
                self.assertTrue(segment['location'])

    def test_root_replacement_after_manifest_does_not_parse_replacement_bytes(self):
        replacement = self.root / 'replacement'; replacement.mkdir()
        (replacement / 'a.txt').write_text('REPLACEMENT ROOT SENTINEL')
        original_manifest = files._manifest
        def swap(*args, **kwargs):
            result = original_manifest(*args, **kwargs)
            self.inputs.rename(self.root / 'previous-root')
            replacement.rename(self.inputs)
            return result
        with patch.object(files, '_manifest', side_effect=swap), patch.object(files, 'parse_file', wraps=files.parse_file) as parse:
            with self.assertRaisesRegex(RuntimeError, 'source_changed'):
                files.file_set_snapshot(self.store.target(self.target), self.manifest)
        parse.assert_not_called()
        self.assertEqual(self.versions(), [])

    def test_root_replacement_between_files_uses_pinned_root_then_rejects_publication(self):
        self.input('b.txt', b'ORIGINAL SECOND FILE')
        self.rows.append({'id': 'two', 'path': 'b.txt'}); self.save_manifest()
        replacement = self.root / 'replacement'; replacement.mkdir()
        (replacement / 'b.txt').write_bytes(b'REPLACEMENT SECOND FILE')
        parse_file = files.parse_file
        observed = []
        def parse(data, filename, **kwargs):
            observed.append(data)
            result = parse_file(data, filename, **kwargs)
            if len(observed) == 1:
                self.inputs.rename(self.root / 'previous-root')
                replacement.rename(self.inputs)
            return result
        with patch.object(files, 'parse_file', side_effect=parse):
            with self.assertRaisesRegex(RuntimeError, 'source_changed'):
                collect_target(self.store, self.target)
        self.assertEqual(observed[1], b'ORIGINAL SECOND FILE')
        self.assertNotIn(b'REPLACEMENT SECOND FILE', observed)
        self.assertEqual(self.versions(), [])

    def test_untruncated_but_incomplete_scope_is_false_in_source_and_capture(self):
        parse_file = files.parse_file
        def omitted(data, filename, **kwargs):
            value = parse_file(data, filename, **kwargs)
            value['scope']['body_complete'] = False
            value['scope']['text_parts_omitted'] = ['controlled omitted text part']
            return value
        with patch.object(files, 'parse_file', side_effect=omitted):
            result = collect_target(self.store, self.target)
            self.assertFalse(result['source']['body_complete'])
            capture_source(self.store, self.target)
        cursor = json.loads(self.store.target(self.target)['cursor'])
        folder = self.store.root / 'source-captures'
        manifest = json.loads((folder / cursor['source_capture']['manifest']).read_bytes())
        self.assertFalse(manifest['original_files'][0]['body_complete'])

    def test_child_projection_requires_matching_segments_and_boolean_coverage(self):
        for coverage in (None, 'true', 1, {}):
            with self.subTest(coverage=coverage):
                value = files.parse_file(b'first\nsecond', 'a.txt')
                value['scope']['body_complete'] = coverage
                with self.assertRaisesRegex(ValueError, 'parser_invalid'):
                    files._validate_parsed(value)
        value = files.parse_file(b'first\nsecond', 'a.txt')
        value['segments'].reverse()
        with self.assertRaisesRegex(ValueError, 'parser_invalid'):
            files._validate_parsed(value)

    def test_linked_pptx_notes_are_captured_with_locations_and_honest_body_coverage(self):
        presentation = io.BytesIO()
        parts = {
            'ppt/presentation.xml': '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="r1"/></p:sldIdLst></p:presentation>',
            'ppt/_rels/presentation.xml.rels': '<Relationships><Relationship Id="r1" Target="slides/slide1.xml"/></Relationships>',
            'ppt/slides/slide1.xml': '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>Slide body</a:t></a:r></a:p></p:sld>',
            'ppt/slides/_rels/slide1.xml.rels': '<Relationships><Relationship Id="notes" Target="../notesSlides/notesSlide1.xml"/></Relationships>',
            'ppt/notesSlides/notesSlide1.xml': '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>Notes-only instruction 7429</a:t></a:r></a:p></p:notes>',
        }
        with zipfile.ZipFile(presentation, 'w') as archive:
            for name, text in parts.items(): archive.writestr(name, text)
        content = presentation.getvalue(); self.input('slides.pptx', content)
        self.rows = [{'id': 'one', 'path': 'slides.pptx'}]; self.save_manifest()
        result = collect_target(self.store, self.target)
        self.assertFalse(result['source']['body_complete'])
        self.assertEqual(len(self.store.search('7429', projects=['one'])), 1)
        capture_source(self.store, self.target)
        cursor = json.loads(self.store.target(self.target)['cursor'])
        folder = self.store.root / 'source-captures'
        manifest = json.loads((folder / cursor['source_capture']['manifest']).read_bytes())
        packet = json.loads((folder / manifest['source_file']).read_bytes())
        parsed = packet['files'][0]['record']['parsed']
        self.assertIn('Notes-only instruction 7429', parsed['text'])
        self.assertEqual(parsed['segments'][-1]['location'], {'kind':'paragraph','part':'ppt/notesSlides/notesSlide1.xml','slide':1,'slide_id':'256','paragraph':1})
        self.assertTrue(parsed['scope']['notes_read'])
        self.assertFalse(manifest['original_files'][0]['body_complete'])
        digest = hashlib.sha256(content).hexdigest()
        self.assertEqual(base64.b64decode(packet['blobs_base64'][digest]), content)


if __name__ == '__main__':unittest.main()
