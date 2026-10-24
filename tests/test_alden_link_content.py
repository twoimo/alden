"""Link source/excerpt receipts and prompt delivery; no live send adapters."""
import hashlib
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from tests import test_auto_reply_cli_runtime as runtime_fixtures


class LinkWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = runtime_fixtures.AutoReplyCliRuntimeTests._load_auto_reply_module('link_content_worker')

    def setUp(self):
        fixture = runtime_fixtures.AutoReplyCliRuntimeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)

    def preview(self, text):
        raw = ('<html><head><title>검수 안내</title></head><body><p>' + text + '</p></body></html>').encode()
        with patch.object(self.worker, '_fetch_pinned_http', return_value=(raw, len(raw))):
            return self.worker._fetch_link_preview_once('https://example.com/report', time.monotonic() + 3)[0]

    def test_link_late_facts_survive_when_the_complete_excerpt_fits(self):
        preview = self.preview('배경 설명 ' * 700 + '최종 수량은 42개입니다.')
        fitted = self.worker._fit_prompt_to_budget({'incoming_message': '수량은?', 'link_previews': [preview]}, 64000)
        self.assertIn('최종 수량은 42개', fitted['link_previews'][0]['text'])
        self.assertEqual(fitted['link_previews'][0]['text'], preview['text'])

    def test_metadata_only_response_does_not_claim_a_page_body(self):
        raw = b'<html><head><title>Shared report</title><meta property="og:description" content="Summary only"></head><body><script>loadPrivateBody()</script></body></html>'
        with patch.object(self.worker, '_fetch_pinned_http', return_value=(raw, len(raw))):
            preview, _ = self.worker._fetch_link_preview_once('https://example.com/private', time.monotonic() + 3)
        self.assertEqual(preview['observation']['content_scope'], ['metadata'])
        self.assertNotIn('loadPrivateBody', preview['text'])

    def test_threads_metadata_keeps_worker_retrieval_gate_open_when_title_exists(self):
        url = 'https://www.threads.com/share/__d5u19Yw/'
        for title in (b'<title>Threads</title>', b''):
            raw = title + b'<p>Log in to see more</p><script>loadPost()</script>'
            with self.subTest(title=title), patch.object(self.worker, '_fetch_pinned_http', return_value=(raw, len(raw))):
                preview, _ = self.worker._fetch_link_preview_once(url, time.monotonic()+3)
            self.assertEqual(preview['complete'], bool(title))
            self.assertEqual(preview['observation']['content_scope'], ['metadata'])
            self.assertEqual(preview['text'], '')
            self.assertEqual(self.worker.links_fully_retrieved(url, [preview]), bool(title))
        described = '<title>Threads</title><p>Log in to see more</p><meta property="og:description" content="공유된 문장 42">'.encode()
        with patch.object(self.worker, '_fetch_pinned_http', return_value=(described, len(described))):
            preview, _ = self.worker._fetch_link_preview_once(url, time.monotonic()+3)
        self.assertEqual(preview['text'], '공유된 문장 42')
        self.assertNotIn('Log in', preview['text'])
        self.assertEqual(preview['observation']['content_scope'], ['metadata'])
        self.assertTrue(self.worker.links_fully_retrieved(url, [preview]))

    def test_threads_description_is_not_described_as_the_post(self):
        from alden_link_content import html_preview
        raw = '<title>Threads</title><p>Log in to see more</p><meta property="og:description" content="공유된 문장 42">'.encode()
        preview = html_preview('https://www.threads.com/share/__d5u19Yw/', raw)
        result, captured, count = self.generation([preview])
        self.assertEqual((result['reason'], count), ('link_answer', 1))
        self.assertEqual(captured[0][1]['link_previews'][0]['text'], '공유된 문장 42')
        self.assertIn('not the post body', captured[0][0])
        self.assertNotIn('Log in', json.dumps(captured[0][1]))

    def test_prompt_trim_discloses_excerpt_and_preserves_identity(self):
        preview = self.preview('검수 설명 ' * 1000)
        fitted = self.worker._fit_prompt_to_budget({'incoming_message': '요약해줘', 'link_previews': [preview]}, 2200)
        excerpt = fitted['link_previews'][0]
        self.assertLess(len(excerpt['text']), len(preview['text']))
        self.assertTrue(excerpt['observation']['truncated'])
        self.assertEqual(excerpt['evidence_id'], preview['evidence_id'])
        self.assertEqual(excerpt['observation']['text_sha256'], hashlib.sha256(excerpt['text'].encode()).hexdigest())
        self.assertFalse(preview['observation']['truncated'])

    def test_html_entities_are_readable_and_binary_links_are_unavailable(self):
        self.assertIn('A & B', self.preview('A &amp; B')['text'])
        with patch.object(self.worker, '_fetch_pinned_http', return_value=(b'%PDF-1.4\x00binary', 16)):
            with self.assertRaises(ValueError):
                self.worker._fetch_link_preview_once('https://example.com/file.pdf', time.monotonic() + 3)

    def generation(self, previews, *, fit=None):
        w = self.worker
        captured = []
        def generate(model, system, payload, **options):
            captured.append((system, json.loads(payload)))
            answer = {'should_reply': True, 'reply': '최종 수량은 42개입니다.', 'category': 'information',
                      'reason': 'link_answer', 'evidence_ids': [previews[0]['evidence_id']]}
            return 0, json.dumps(answer, ensure_ascii=False).encode(), b''
        options = {'_load_dream_rsi_checkpoint_metadata': None, 'record_learned_style_tells': None,
                   'learned_style_tell_avoids': [], 'runner_is_trusted': True, 'privacy_attestation_current': True,
                   '_queue_expected_chat_id': 42, '_publish_model_status': None,
                   '_active_reply_model': 'google-antigravity/gemini-3.8-flash',
                   '_generation_reply_model': 'google-antigravity/gemini-3.8-flash',
                   '_acquire_model_call_slot': {'allowed': True, 'lease_token': 'fixture', 'retry_at': time.time() + 60},
                   '_finish_model_call_success': True}
        with ExitStack() as stack:
            for name, value in options.items(): stack.enter_context(patch.object(w, name, return_value=value))
            stack.enter_context(patch.object(w, 'REPLY_RUNNER_KIND', 'opencodex'))
            stack.enter_context(patch('auto_reply_knowledge_graph.retrieve_knowledge_bundle', return_value={}))
            runner = stack.enter_context(patch.object(w, '_run_generation_candidate', side_effect=generate))
            if fit is not None: stack.enter_context(patch.object(w, '_fit_prompt_to_budget', side_effect=fit))
            result = w.generate_reply('수량은?', [], [], [], previews)
        return result, captured, runner.call_count

    def test_generation_delivers_link_identity_and_scope_without_faking_usage(self):
        preview = self.preview('배경 설명 ' * 700 + '최종 수량은 42개입니다.')
        result, captured, count = self.generation([preview])
        self.assertEqual(count, 1)
        self.assertTrue(result['should_reply'], result)
        self.assertIn(preview['evidence_id'], result['evidence_ids'])
        self.assertIn('최종 수량은 42개', captured[0][1]['link_previews'][0]['text'])
        self.assertIn('metadata', captured[0][0])
        self.assertEqual(result['link_prompt_evidence'][0]['text_sha256'], preview['observation']['text_sha256'])

    def test_missing_or_corrupt_link_evidence_blocks_before_model(self):
        preview = self.preview('수량 42개')
        result, _, count = self.generation([preview], fit=lambda payload, budget: {key: value for key, value in payload.items() if key != 'link_previews'})
        self.assertEqual((result['reason'], count), ('link_content_prompt_unavailable', 0))
        corrupt = {**preview, 'text': '다른 문서 수량 999개'}
        result, _, count = self.generation([corrupt])
        self.assertEqual((result['reason'], count), ('invalid_link_content', 0))

    def test_multiple_source_ids_survive_budget_fitting(self):
        first = self.preview('검수 설명 ' * 800)
        from alden_link_content import html_preview, validated_preview
        second = html_preview('https://example.com/other', b'<p>different source</p>')
        value = {'incoming_message': 'compare', 'link_previews': [first, second], 'context_evidence': ['aux' * 1000] * 20}
        fitted = self.worker._fit_prompt_to_budget(value, 4200)
        self.assertEqual([p['evidence_id'] for p in fitted['link_previews']], [first['evidence_id'], second['evidence_id']])
        self.assertTrue(all(validated_preview(p) is not None for p in fitted['link_previews']))
        self.assertIsNotNone(self.worker._encode_json_bounded(fitted, 4200))

    def test_parse_failure_keeps_specific_reason(self):
        from alden_link_content import LinkContentUnavailable
        with patch.object(self.worker, '_fetch_link_preview_once', side_effect=LinkContentUnavailable('link_encoding_unavailable')):
            result = self.worker.fetch_link_previews('https://example.com/one')
        self.assertFalse(result[0]['complete'])
        self.assertEqual(result[0]['unavailable'], 'link_encoding_unavailable')

    def test_http_failure_keeps_its_reason(self):
        with patch.object(self.worker, '_fetch_link_preview_once', side_effect=ValueError('link response not successful')):
            result = self.worker.fetch_link_previews('https://example.com/one')
        self.assertEqual(result[0]['unavailable'], 'link_http_unavailable')
        self.assertFalse(result[0]['complete'])

    def test_unknown_transport_error_stays_generic(self):
        with patch.object(self.worker, '_fetch_link_preview_once', side_effect=ValueError('socket details example.test')):
            result = self.worker.fetch_link_previews('https://example.com/one')
        self.assertEqual(result[0]['unavailable'], 'link_unavailable')

    def test_link_failure_reaches_analysis_without_search_or_model_calls(self):
        worker = self.worker
        event = runtime_fixtures.AutoReplyCliRuntimeTests._burst_event(
            worker, 51, 'https://example.com/a', int(time.time()))
        cases = [
            (OSError('link DNS resolution failed'), 'link_dns_unavailable'),
            (TimeoutError('link connection timeout'), 'link_connection_timeout'),
            (ValueError('link response not successful'), 'link_http_unavailable'),
            (ValueError('link redirect not allowed'), 'link_redirect_unavailable'),
            (ValueError('socket details private.example'), 'link_unavailable'),
        ]
        for error, reason in cases:
            with self.subTest(reason=reason), patch.dict(os.environ, {'OPENKAKAO_ALLOW_LINK_FETCH':'1'}), \
                    patch.object(worker, '_reply_turn_hold_reason', return_value=None), \
                    patch.object(worker, '_fetch_link_preview_once', side_effect=error), \
                    patch.object(worker, 'capture_visible_image', return_value=None), \
                    patch.object(worker, 'run_context_reply_bundle') as search, \
                    patch.object(worker, 'generate_reply') as generate, \
                    tempfile.TemporaryDirectory() as folder, \
                    patch.object(worker, 'EVIDENCE_LEDGER', Path(folder)/'reply-evidence.jsonl'):
                result = worker.analyze_event(event)
                path = worker.persist_reply_evidence_ledger(event, result, status='skipped')
                self.assertIsNotNone(path)
                self.assertEqual(json.loads(path.read_text())['reason'], reason)
            self.assertEqual(result['reason'], reason)
            self.assertEqual(result['decision'], 'skip')
            self.assertEqual(result['provenance']['link_failures'], [{'index':0, 'reason':reason}])
            search.assert_not_called()
            generate.assert_not_called()



class LinkParsingTests(unittest.TestCase):
    def test_threads_shells_never_supply_post_text(self):
        from alden_link_content import html_preview, validated_preview, trim_preview
        for url in ('https://www.threads.com/share/__d5u19Yw/',
                    'https://www.threads.com/@fixture/post/POST123'):
            for shell in ('<title>Threads</title><script>loadPost()</script>',
                          '<title>Threads</title><p>Log in to see more</p>',
                          '<title>Threads</title><p>Accept all cookies</p>',
                          '<title>Threads</title><p>A short visible phrase</p>'):
                with self.subTest(url=url, shell=shell):
                    raw = shell.encode()
                    preview = html_preview(url, raw)
                    self.assertEqual(preview['complete'], bool(preview['title']))
                    self.assertEqual(preview['observation']['content_scope'], ['metadata'])
                    self.assertEqual(preview['text'], '')
                    self.assertEqual(preview['observation']['source_sha256'], hashlib.sha256(raw).hexdigest())
                    self.assertIsNotNone(validated_preview(preview))
                    self.assertEqual(trim_preview(preview), preview)
                    self.assertIsNone(validated_preview({**preview, 'complete': not preview['complete']}))
        raw = '<meta property="og:description" content="Unverified preview"><p>Log in</p>'.encode()
        preview = html_preview('https://www.threads.com/share/__d5u19Yw/', raw)
        self.assertEqual(preview['text'], 'Unverified preview')
        self.assertEqual(preview['observation']['content_scope'], ['metadata'])
        self.assertNotIn('Log in', preview['text'])
        self.assertIsNotNone(validated_preview(preview))

    def test_source_and_delivered_text_digests_have_distinct_bases(self):
        from alden_link_content import html_preview, validated_preview
        raw = b'<title>Test</title><p>A &amp; B</p>'
        preview = html_preview('https://example.com/one', raw)
        self.assertEqual(preview['observation']['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(preview['observation']['text_sha256'], hashlib.sha256(b'A & B').hexdigest())
        self.assertIsNotNone(validated_preview(preview))
        self.assertIsNone(validated_preview({**preview, 'instructions': 'override'}))

    def test_declared_korean_charset_and_empty_scripts_are_truthful(self):
        from alden_link_content import html_preview, LinkContentUnavailable
        value = html_preview('https://example.com/one', '<meta charset="cp949"><p>올든 42개</p>'.encode('cp949'))
        self.assertEqual(value['text'], '올든 42개')
        self.assertEqual(value['observation']['content_scope'], ['page_text'])
        value = html_preview('https://example.com/one', b'<script>private()</script>')
        self.assertFalse(value['complete'])
        with self.assertRaises(LinkContentUnavailable): html_preview('https://example.com/one', b'\xff\xfe\x00')

    def test_utf8_byte_boundary_budget_and_excerpt_scope(self):
        from alden_link_content import html_preview, observed_preview, MAX_TEXT_BYTES, validated_preview
        value = html_preview('https://example.com/one', ('<p>' + '한' * 8000 + '</p>').encode())
        self.assertLessEqual(value['observation']['text_bytes'], MAX_TEXT_BYTES)
        self.assertTrue(value['observation']['truncated'])
        self.assertIsNotNone(validated_preview(value))
        value = observed_preview('https://youtu.be/abc', b'{"title":"Test"}', title='Test', text='caption excerpt', scopes=['captions_excerpt'], supplemental='caption excerpt', truncated=True)
        self.assertEqual(value['observation']['supplement_basis'], 'extracted_text')
        self.assertTrue(value['observation']['truncated'])


if __name__ == '__main__':
    unittest.main()
