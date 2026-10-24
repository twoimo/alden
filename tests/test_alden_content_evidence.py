"""Source declarations gate candidates; they never certify semantic truth."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_content_evidence import content_evidence_class


class ContentEvidenceTests(unittest.TestCase):
    def test_empty_or_whitespace_body_cannot_be_upgraded_by_index_or_title(self):
        for body in ('', ' ', '\n\t', '\u3000\u00a0'):
            for metadata in ({}, {'content_indexed': True}, {'label': '실제로 보이는 제목'}):
                with self.subTest(body=body, metadata=metadata):
                    self.assertEqual(content_evidence_class(metadata, body,
                        source={'kind': 'video', 'title': '실제로 보이는 제목'}), 'empty_body')

    def test_explicit_noncontent_precedence_is_retained_for_empty_body(self):
        self.assertEqual(content_evidence_class({'kind': 'metadata'}, ''), 'metadata_only')
        self.assertEqual(content_evidence_class({}, '', source={'content_indexed': False}),
                         'source_metadata_only')

    def test_short_nonempty_authored_body_is_not_an_empty_body(self):
        for body in ('0', '사과', 'Yes', '1. 절차를 확인한다.'):
            with self.subTest(body=body):
                self.assertEqual(content_evidence_class({}, body), 'content_candidate')

    def test_addresses_are_references_even_when_indexed_or_source_bytes_are_verified(self):
        for body in ('https://example.org/a?edition=2#section',
                     ' HTTP://example.org/A\nhttps://example.org/B ',
                     'https://example.org/한글?q=공기업'):
            with self.subTest(body=body):
                self.assertEqual(content_evidence_class({}, body), 'reference_only')
                self.assertEqual(content_evidence_class({'content_indexed': True}, body,
                    source={'content_indexed': True, 'localOriginalText': body},
                    body_source='retained_record.localOriginalText'), 'reference_only')

    def test_prose_quotes_and_short_authored_text_remain_content_candidates(self):
        for body in ('채용 공고는 https://example.org/apply 에서 확인한다.',
                     'URL 자체에 대한 주장: https://example.org',
                     '"https://example.org"', '사과', '1. 절차를 확인한다.'):
            with self.subTest(body=body):
                self.assertEqual(content_evidence_class({}, body), 'content_candidate')

    def test_reference_shape_does_not_override_noncontent_declarations(self):
        body='https://example.org/apply'
        self.assertEqual(content_evidence_class({'kind':'metadata'}, body), 'metadata_only')
        self.assertEqual(content_evidence_class({}, body, source={'content_indexed':False}), 'source_metadata_only')

    def test_derived_summary_declaration_does_not_certify_original_body(self):
        metadata = {'coverage': 'existing privacy-filtered derived metadata; original bytes not opened'}
        self.assertEqual(content_evidence_class(metadata, 'Shop chassis pointer'),
                         'metadata_description')

    def test_equal_title_or_short_authored_body_is_not_blanket_excluded(self):
        self.assertEqual(content_evidence_class({'label': 'Yes', 'kind': 'document'}, 'Yes'),
                         'content_candidate')

    def test_verified_retained_body_can_replace_summary_but_not_noncontent_guards(self):
        metadata = {'coverage': 'existing privacy-filtered derived metadata; original bytes not opened'}
        self.assertEqual(content_evidence_class(metadata, 'Actual preserved body',
                         body_source='retained_record.localOriginalText'), 'content_candidate')
        self.assertEqual(content_evidence_class({**metadata, 'content_indexed': 0}, 'Actual preserved body',
                         body_source='retained_record.localOriginalText'), 'metadata_only')
        self.assertEqual(content_evidence_class(metadata, '경로만 색인한 파일. 본문은 읽지 않았습니다.',
                         body_source='retained_record.localOriginalText'), 'unread_body_marker')
        with self.assertRaisesRegex(ValueError, '^content_evidence_invalid$'):
            content_evidence_class(metadata, 'body', body_source='unknown-field')

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
