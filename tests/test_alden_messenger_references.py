"""Explicit reply observations through controlled canonical receipts; no native IO."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import copy
import hashlib
import json
import sys
import unittest
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_messenger_runtime import KakaoReader, SourceBlocked


class ReferenceTests(unittest.TestCase):
    def response(self, raw=None):
        raw = raw or json.dumps({'src_logId': '7', 'src_message': 'private quoted text',
                                 'unrelated_secret_locator': 'private locator'}).encode()
        return {'status': 'ok', 'db_readonly_verified': True, 'schema_decryption_verified': True,
                'server_calls': 0, 'send_calls': 0, 'database_row_writes': 0, 'app_settings_writes': 0,
                'chat': {'chatId': '123'}, 'messages_truncated': False,
                'messages': [{'chatId': '123', 'logId': '9', 'type': 26, 'message': '회의 자료 검토 결과',
                    'media': {'kind': 'reply', 'state': 'quoted_context',
                              'attachment_sha256': hashlib.sha256(raw).hexdigest()}, 'rawAttachment': raw}]}

    def invoke(self, response, identities=('owner', 'owner')):
        calls = []
        reader = KakaoReader(SimpleNamespace(snapshot=lambda **options: calls.append(options) or copy.deepcopy(response)))
        with patch.object(reader, 'identity', side_effect=identities):
            result = reader.reply_reference('123', '9')
        return result, calls

    def test_exact_reference_has_hash_bound_source_without_quoted_body_or_locator(self):
        proof, calls = self.invoke(self.response())
        self.assertEqual(proof['state'], 'verified')
        self.assertEqual((proof['room_id'], proof['source_message_id'], proof['target_message_id']), ('123', '9', '7'))
        self.assertEqual(calls, [{'chat_id': '123', 'message_id': '9', 'limit': 1,
                                 'show_text': True, 'include_media': True, 'include_raw_attachment': True}])
        self.assertNotIn('private', json.dumps(proof))
        self.assertNotIn('회의', json.dumps(proof, ensure_ascii=False))

    def test_same_body_changed_reference_changes_reference_revision(self):
        before, _ = self.invoke(self.response())
        after, _ = self.invoke(self.response(json.dumps({'src_logId': '8', 'src_message': 'quote'}).encode()))
        self.assertEqual(before['source_content_hash'], after['source_content_hash'])
        self.assertNotEqual(before['reference_revision'], after['reference_revision'])

    def test_nfc_body_hash_matches_content_identity(self):
        response = self.response(); response['messages'][0]['message'] = unicodedata.normalize('NFD', '회의 자료')
        proof, _ = self.invoke(response)
        self.assertEqual(proof['source_content_hash'], hashlib.sha256('회의 자료'.encode()).hexdigest())

    def test_withheld_reply_or_quote_never_exposes_source_or_target_identifiers(self):
        for state in ('deleted', 'quoted_content_withheld', 'quoted_source_unverified'):
            with self.subTest(state=state):
                response = self.response()
                if state == 'deleted': response['messages'][0]['message_state'] = state
                else: response['messages'][0]['media']['state'] = state
                proof, _ = self.invoke(response)
                self.assertEqual(proof['state'], 'withheld')
                self.assertNotIn('source_message_id', proof); self.assertNotIn('target_message_id', proof)

    def test_code26_alone_is_not_reply_evidence(self):
        response = self.response(); response['messages'][0]['media'] = {'kind': 'file', 'state': 'metadata_only'}
        proof, _ = self.invoke(response)
        self.assertEqual(proof['state'], 'not_admitted')

    def test_account_change_readonly_or_side_effect_receipt_blocks(self):
        with self.assertRaisesRegex(SourceBlocked, 'account_changed'):
            self.invoke(self.response(), identities=('owner', 'other'))
        for key in ('db_readonly_verified', 'schema_decryption_verified', 'server_calls', 'send_calls',
                    'database_row_writes', 'app_settings_writes'):
            with self.subTest(key=key):
                response = self.response(); response[key] = False if key.endswith('verified') else 1
                with self.assertRaisesRegex(SourceBlocked, 'readonly_unconfirmed'): self.invoke(response)

    def test_wrong_row_room_or_truncated_exact_id_response_blocks(self):
        for change in ('room', 'row_room', 'id', 'duplicate', 'truncated', 'chat_shape'):
            with self.subTest(change=change):
                response = self.response()
                if change == 'room': response['chat']['chatId'] = '456'
                elif change == 'row_room': response['messages'][0]['chatId'] = '456'
                elif change == 'id': response['messages'][0]['logId'] = '10'
                elif change == 'duplicate': response['messages'] *= 2
                elif change == 'chat_shape': response['chat'] = []
                else: response['messages_truncated'] = True
                with self.assertRaisesRegex(SourceBlocked, 'scope_mismatch'): self.invoke(response)

    def test_malformed_duplicate_oversized_or_mismatched_raw_metadata_blocks(self):
        for raw in (b'{', b'{"src_logId":"7","src_logId":"8","src_message":"q"}', b'[' * 262145,
                    b'{"src_logId":true,"src_message":"q"}',
                    b'{"src_logId":"9223372036854775808","src_message":"q"}',
                    b'{"src_logId":"0","src_message":"q"}'):
            with self.subTest(raw_length=len(raw)):
                with self.assertRaisesRegex(SourceBlocked, 'unverified'): self.invoke(self.response(raw))
        response = self.response(); response['messages'][0]['media']['attachment_sha256'] = '0' * 64
        with self.assertRaisesRegex(SourceBlocked, 'unverified'): self.invoke(response)
        for key in ('message', 'rawAttachment'):
            response = self.response(); response['messages'][0][key] = '\ud800'
            with self.assertRaisesRegex(SourceBlocked, 'unverified'): self.invoke(response)

    def test_unavailable_receipt_is_not_absence_and_bad_scope_makes_no_request(self):
        with self.assertRaisesRegex(SourceBlocked, 'unavailable'): self.invoke({'status': 'unavailable'})
        calls = []
        reader = KakaoReader(SimpleNamespace(snapshot=lambda **options: calls.append(options)))
        for room, message in [('room', '9'), ('123', '0'), ('123', '９'), ('123', '9223372036854775808')]:
            with self.assertRaisesRegex(ValueError, 'scope_invalid'): reader.reply_reference(room, message)
        self.assertEqual(calls, [])


if __name__ == '__main__': unittest.main()
