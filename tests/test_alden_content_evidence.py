"""Source declarations gate candidates; they never certify semantic truth."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_content_evidence import content_evidence_class


class ContentEvidenceTests(unittest.TestCase):
    def test_whole_code_or_directory_description_does_not_certify_body_content(self):
        for kind, body in [('code', '현재 체크아웃의 소스 파일'), ('hub', '파일 시스템의 디렉터리')]:
            with self.subTest(kind=kind):
                self.assertEqual(content_evidence_class({'kind': kind}, body), 'metadata_description')
                self.assertEqual(content_evidence_class({}, body, source={'kind': kind, 'content_indexed': 1}),
                                 'metadata_description')
                self.assertEqual(content_evidence_class({'kind': kind}, '인용: '+body+' 이후 실제 내용을 설명한다.'),
                                 'content_candidate')
        self.assertEqual(content_evidence_class({'kind': 'document'}, '현재 체크아웃의 소스 파일'), 'content_candidate')

    def test_verified_source_declaration_is_not_lost_when_the_projection_omits_it(self):
        self.assertEqual(content_evidence_class({'kind': 'hub'}, 'Group description',
                         source={'kind': 'hub', 'content_indexed': 0}), 'source_metadata_only')
        self.assertEqual(content_evidence_class({}, 'Real retained text',
                         source={'content_indexed': True}), 'content_candidate')
        self.assertEqual(content_evidence_class({}, 'Real retained text',
                         source='unknown source shape'), 'content_candidate')

    def test_explicit_noncontent_declarations(self):
        for metadata in ({'kind': 'metadata'}, {'content_indexed': False}, {'content_indexed': 0}):
            with self.subTest(metadata=metadata):
                self.assertEqual(content_evidence_class(metadata, 'A filename description'), 'metadata_only')

    def test_notice_must_cover_the_whole_body(self):
        marker = '경로만 색인한 파일. 본문은 읽지 않았습니다.'
        self.assertEqual(content_evidence_class({}, '\n' + marker + '\n'), 'unread_body_marker')
        self.assertEqual(content_evidence_class({}, '인용: ' + marker + ' 후속 설명'), 'content_candidate')

    def test_missing_or_unknown_declarations_do_not_certify_content(self):
        for metadata in ({}, {'content_indexed': True}, {'content_indexed': '0'}, {'content_indexed': 0.0}):
            with self.subTest(metadata=metadata):
                self.assertEqual(content_evidence_class(metadata, 'Observed body'), 'content_candidate')

    def test_invalid_inputs_are_not_silently_admitted(self):
        for metadata, body in ((None, 'body'), ([], 'body'), ({}, None)):
            with self.subTest(metadata=metadata, body=body):
                with self.assertRaisesRegex(ValueError, '^content_evidence_invalid$'):
                    content_evidence_class(metadata, body)
