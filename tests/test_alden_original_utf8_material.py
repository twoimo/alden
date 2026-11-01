"""Pure original material and the shared bounded-window verification contract."""
import base64
import copy
import inspect
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from tests.test_alden_original_utf8_window import fixture, add_sibling, encoded, sha
import alden_file_set as files


class OriginalUtf8MaterialTests(unittest.TestCase):
    def material(self, values=None, **options):
        packet, record, originals = fixture() if values is None else values
        return files.original_utf8_material(packet, record=record,
            record_pointer=options.pop('record_pointer', '/files/0/record'), original_files=originals, **options)

    def test_import_api_signature_and_exact_fresh_result(self):
        signature = inspect.signature(files.original_utf8_material)
        self.assertEqual(list(signature.parameters), ['packet', 'record', 'record_pointer', 'original_files', 'cancelled'])
        for key in list(signature.parameters)[1:]:
            self.assertEqual(signature.parameters[key].kind, inspect.Parameter.KEYWORD_ONLY)
        first, second = self.material(), self.material()
        self.assertEqual(set(first), {'data', 'text'})
        self.assertIs(type(first['data']), bytes)
        self.assertIs(type(first['text']), str)
        self.assertEqual(first['data'], b'First line\r\nSecond line\n')
        self.assertEqual(first['text'].encode(), first['data'])
        self.assertIsNot(first, second)

    def test_bom_nfd_crlf_cr_tabs_spaces_and_final_newline_remain_exact(self):
        data = '\ufeffe\u0301 한글😀\r\n\t  Markdown  \rfinal \n'.encode()
        values = fixture(data)
        result = self.material(values)
        self.assertEqual(result['data'], data)
        self.assertEqual(result['text'], '\ufeffe\u0301 한글😀\r\n\t  Markdown  \rfinal \n')
        self.assertNotEqual(result['text'], files.normalized_body(result['text']))
        self.assertEqual(sha(result['data']), values[1]['input_sha256'])

    def test_tail_is_not_limited_to_parser_or_window_and_coverage_is_unchanged(self):
        data = b'head\n' + b'A' * 20000 + b'\ntailnonce\n'
        values = fixture(data); before = copy.deepcopy(values)
        self.assertTrue(values[1]['parsed']['truncated'])
        self.assertFalse(values[2][0]['body_complete'])
        material = self.material(values)
        self.assertTrue(material['data'].endswith(b'tailnonce\n'))
        self.assertEqual(material['data'], data)
        window = files.original_utf8_window(values[0], record=values[1], record_pointer='/files/0/record', original_files=values[2])
        self.assertLessEqual(len(window['text'].encode()), 16000)
        self.assertNotIn('tailnonce', window['text'])
        self.assertEqual(values, before)

    def test_exact_five_mib_with_unicode_tail_and_one_byte_over(self):
        suffix = 'e\u0301가😀\r\n'.encode()
        data = b'A' * (files.MAX_FILE_BYTES - len(suffix)) + suffix
        values = fixture(data)
        result = self.material(values)
        self.assertEqual(len(result['data']), 5 * 1024 * 1024)
        self.assertEqual(result['text'].encode(), data)
        self.assertEqual(sha(result['data']), values[1]['input_sha256'])
        over = fixture(data + b'B')
        with self.assertRaises(files.SourceBodyError) as caught:
            self.material(over)
        self.assertEqual(caught.exception.code, 'alden_source_body_integrity')

    def test_mutating_return_dict_never_mutates_packet_or_later_result(self):
        values = fixture(); before = encoded(values)
        result = self.material(values)
        result['data'] = b'forged'; result['text'] = 'forged'; result['extra'] = True
        again = self.material(values)
        self.assertEqual(again, {'data': b'First line\r\nSecond line\n', 'text': 'First line\r\nSecond line\n'})
        self.assertEqual(encoded(values), before)
        with self.assertRaises(TypeError):
            again['data'][0] = 1

    def test_integrity_shape_binding_sha_and_canonical_base64_fail_without_fallback(self):
        changes = [lambda p,r,o:p.update(schema=True), lambda p,r,o:p.update(root='/wrong'),
                   lambda p,r,o:p.update(manifest_sha256='f' * 64),
                   lambda p,r,o:r.update(file_id='other'), lambda p,r,o:r.update(input_bytes=r['input_bytes'] + 1),
                   lambda p,r,o:r.update(parser_code_sha256='bad'),
                   lambda p,r,o:p['files'][0].update(blob_sha256='f' * 64),
                   lambda p,r,o:p['blobs_base64'].update({r['input_sha256']: base64.b64encode(b'X' * r['input_bytes']).decode()}),
                   lambda p,r,o:o[0].update(body_complete=False), lambda p,r,o:o[0].update(json_pointer='/wrong')]
        for change in changes:
            values = fixture(); change(*values)
            with self.subTest(change=changes.index(change)), self.assertRaises(files.SourceBodyError) as caught:
                self.material(values)
            self.assertEqual(caught.exception.code, 'alden_source_body_integrity')
            self.assertEqual(str(caught.exception), caught.exception.code)
        values = fixture(b'f'); values[0]['blobs_base64'][values[1]['input_sha256']] = 'Zh=='
        self.assertEqual(base64.b64decode('Zh==', validate=True), b'f')
        with self.assertRaises(files.SourceBodyError):
            self.material(values)
        for pointer in ('/files/00/record', '/files/0/../record', '/files/1/record'):
            with self.subTest(pointer=pointer), self.assertRaises(files.SourceBodyError):
                self.material(record_pointer=pointer)

    def test_unsupported_parser_and_encoding_keep_fixed_codes(self):
        for change in (lambda r:r.update(format='.pdf'), lambda r:r['parsed'].update(parser='macos-textutil'),
                       lambda r:r['parsed'].update(processing_version='other-version')):
            values = fixture(); change(values[1])
            with self.assertRaises(files.SourceBodyError) as caught:
                self.material(values)
            self.assertEqual(caught.exception.code, 'alden_source_body_format_unsupported')
        for data in (b'A' * 20000 + b'\xff', b'A' * 20000 + b'\x00', b'A' * 20000 + b'\x7f',
                     ('A' * 20000 + '\u0085').encode(), 'UTF16 text'.encode('utf-16')):
            values = fixture(data, extracted='safe parser preview')
            with self.subTest(tail=data[-8:]), self.assertRaises(files.SourceBodyError) as caught:
                self.material(values)
            self.assertEqual(caught.exception.code, 'alden_source_body_encoding_unsupported')

    def test_only_selected_original_decodes_and_equal_byte_identities_stay_separate(self):
        values = fixture(); add_sibling(values[0], values[2])
        values[0]['blobs_base64'][values[0]['files'][1]['blob_sha256']] = 'not base64'
        with patch.object(files.base64, 'b64decode', wraps=base64.b64decode) as decoded:
            self.material(values)
            self.assertEqual(decoded.call_count, 2)
        values = fixture(); add_sibling(values[0], values[2], data=b'First line\r\nSecond line\n')
        results = [files.original_utf8_material(values[0], record=row['record'], record_pointer=f'/files/{i}/record',
                   original_files=values[2]) for i,row in enumerate(values[0]['files'])]
        self.assertEqual(results[0], results[1]); self.assertIsNot(results[0], results[1])
        self.assertEqual(len(values[0]['blobs_base64']), 1)

    def test_window_uses_material_once_and_does_not_redecode_selected_base64(self):
        values = fixture()
        with patch.object(files, 'original_utf8_material', wraps=files.original_utf8_material) as material, \
             patch.object(files.base64, 'b64decode', wraps=base64.b64decode) as decoded:
            window = files.original_utf8_window(values[0], record=values[1], record_pointer='/files/0/record', original_files=values[2], max_bytes=4)
        self.assertEqual(window['text'], 'Firs')
        material.assert_called_once()
        self.assertEqual(decoded.call_count, 2)
        self.assertEqual(set(window), {'encoding','offset_bytes','end_bytes','next_offset_bytes','total_bytes','text','text_sha256'})

    def test_bad_window_args_reject_before_material_or_decode_or_cancel_work(self):
        with patch.object(files, 'original_utf8_material', side_effect=AssertionError('material reached')) as material, \
             patch.object(files.base64, 'b64decode', side_effect=AssertionError('decode reached')) as decoded:
            for options in ({'offset_bytes': True}, {'offset_bytes': -1}, {'offset_bytes': 5242881},
                            {'offset_bytes': 0.0}, {'max_bytes': 3}, {'max_bytes': 16001},
                            {'max_bytes': False}, {'max_bytes': '4'}, {'cancelled': None}):
                with self.subTest(options=options), self.assertRaisesRegex(ValueError, '^collection_source_body_request_invalid$'):
                    files.original_utf8_window(None, record=None, record_pointer=None, original_files=None, **options)
            material.assert_not_called(); decoded.assert_not_called()
        with self.assertRaisesRegex(ValueError, '^collection_source_body_request_invalid$'):
            self.material(cancelled=None)

    def test_every_material_cancellation_checkpoint_propagates_without_material(self):
        values = fixture(b'valid ' * 4000); checks = []
        self.material(values, cancelled=lambda: checks.append(1) or False)
        self.assertGreaterEqual(len(checks), 10)
        for stop in range(1, len(checks) + 1):
            calls = []
            def cancelled():
                calls.append(1)
                return len(calls) == stop
            with self.subTest(checkpoint=stop), self.assertRaisesRegex(RuntimeError, '^collection_cancelled$'):
                self.material(values, cancelled=cancelled)
            self.assertEqual(len(calls), stop)

    def test_cancel_immediately_after_selected_base64_decode_and_abort_identity(self):
        values = fixture(); original = base64.b64decode; count = [0]
        def decode(*args, **kwargs):
            raw = original(*args, **kwargs); count[0] += 1
            return raw
        with patch.object(files.base64, 'b64decode', decode), self.assertRaisesRegex(RuntimeError, '^collection_cancelled$'):
            self.material(values, cancelled=lambda: count[0] == 2)
        abort = RuntimeError('owned runtime abort')
        def cancelled():
            raise abort
        with self.assertRaises(RuntimeError) as caught:
            self.material(values, cancelled=cancelled)
        self.assertIs(caught.exception, abort)

    def test_material_does_not_call_windows_io_parser_native_or_network(self):
        values = fixture(); before = encoded(values)
        with patch.object(files, 'original_utf8_window', side_effect=AssertionError('window fallback')), \
             patch('builtins.open', side_effect=AssertionError('IO')), \
             patch.object(files.os, 'open', side_effect=AssertionError('IO')), \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('IO')), \
             patch.object(files, '_manifest', side_effect=AssertionError('manifest IO')), \
             patch.object(files, 'file_set_snapshot', side_effect=AssertionError('snapshot')), \
             patch.object(files, 'parse_file', side_effect=AssertionError('parser')), \
             patch.object(files, 'run_owned_json', side_effect=AssertionError('native')), \
             patch.object(files.subprocess, 'Popen', side_effect=AssertionError('process')), \
             patch.object(socket.socket, 'connect', side_effect=AssertionError('network')):
            result = self.material(values)
        self.assertEqual(result['data'], b'First line\r\nSecond line\n')
        self.assertEqual(encoded(values), before)

    def test_material_failure_never_becomes_a_successful_window_fallback(self):
        error = files.SourceBodyError('alden_source_body_integrity')
        with patch.object(files, 'original_utf8_material', side_effect=error), self.assertRaises(files.SourceBodyError) as caught:
            files.original_utf8_window(None, record=None, record_pointer=None, original_files=None)
        self.assertIs(caught.exception, error)


if __name__ == '__main__':
    unittest.main()
