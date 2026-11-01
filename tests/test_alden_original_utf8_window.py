"""Pure retained-original windows: synthetic producer-shaped packets, no IO."""
import base64
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

# A copied pre-change module can be selected for the discriminating baseline.
sys.path.insert(0, os.environ.get('ALDEN_BODY_BASELINE', str(Path(__file__).resolve().parents[1] / 'scripts')))
import alden_file_set as files


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def set_manifest(packet, declarations, *, raw=None):
    # Pretty JSON is a valid producer input; only its base64 must be canonical.
    raw = json.dumps({'schema': 1, 'files': declarations}, ensure_ascii=False, indent=2).encode() if raw is None else raw
    packet['manifest_base64'] = base64.b64encode(raw).decode('ascii')
    packet['manifest_sha256'] = sha(raw)


def fixture(data=b'First line\r\nSecond line\n', *, path='nested/source.md', file_id='one', extracted=None):
    """Construct the current file-set producer's record/packet/receipt shapes."""
    original_text = data.decode('utf-8-sig') if extracted is None else extracted
    normalized = original_text.replace('\r\n', '\n').replace('\r', '\n')
    lines = normalized.split('\n')
    if normalized.endswith('\n'):
        lines.pop()
    joined = '\n'.join(lines[:256])
    bounded = joined.encode()[:16000].decode('utf-8', 'ignore')
    partial = len(lines) > 256 or bounded != joined or extracted is not None
    segments = [{'location': {'kind': 'line', 'line_start': i, 'line_end': i},
                 'text': text, 'text_sha256': sha(text.encode())}
                for i, text in enumerate(bounded.split('\n'), 1)]
    parsed = {'processing_version': 'document-segments-v1', 'parser': 'text-' + Path(path).suffix[1:].lower(),
              'text': bounded, 'text_sha256': sha(bounded.encode()), 'truncated': partial,
              'scope': {'kind': 'original-lines', 'original_positions': True, 'line_endings': 'LF-normalized',
                        'body_complete': not partial, 'lines_total': len(lines), 'segment_count_complete': True,
                        'segment_candidates_seen': len(lines), 'segments_omitted': max(0, len(lines) - 256),
                        'partial_segment': len(segments) if partial else None, 'max_segments': 256,
                        'max_text_bytes': 16000, 'joined_with': 'LF'}, 'segments': segments}
    digest = sha(data)
    entry = {'id': file_id, 'path': path, 'label': '원본 fixture'}
    record = {'schema': 1, 'file_id': file_id, 'path': path, 'source_root': '/declared/fixture',
              'input_sha256': digest, 'input_bytes': len(data), 'mtime_ns': 1700000000000000000,
              'created_at_seconds': None, 'format': Path(path).suffix.lower(),
              'parser_code_sha256': 'c' * 64, 'parsed': parsed}
    packet = {'schema': 1, 'kind': 'declared-original-file-set-v1', 'root': record['source_root'],
              'files': [{'entry': entry, 'record': record, 'blob_sha256': digest}],
              'blobs_base64': {digest: base64.b64encode(data).decode('ascii')},
              'exact_byte_groups': {digest: [file_id]}}
    set_manifest(packet, [entry])
    originals = [{'file_id': file_id, 'sha256': digest, 'json_pointer': '/blobs_base64/' + digest,
                  'encoding': 'base64', 'body_complete': not partial}]
    return packet, record, originals


def add_sibling(packet, originals, *, data=b'sibling', path='sibling.txt', file_id='two'):
    sibling, _record, receipt = fixture(data, path=path, file_id=file_id)
    packet['files'].extend(sibling['files'])
    packet['blobs_base64'].update(sibling['blobs_base64'])
    for digest, ids in sibling['exact_byte_groups'].items():
        packet['exact_byte_groups'].setdefault(digest, []).extend(ids)
        packet['exact_byte_groups'][digest].sort()
    originals.extend(receipt)
    set_manifest(packet, [item['entry'] for item in packet['files']])


class OriginalUtf8WindowTests(unittest.TestCase):
    def call(self, values=None, **options):
        packet, record, originals = fixture() if values is None else values
        return files.original_utf8_window(packet, record=record, record_pointer=options.pop('record_pointer', '/files/0/record'),
                                          original_files=originals, **options)

    def error(self, code, values, **options):
        with self.assertRaises(files.SourceBodyError) as raised:
            self.call(values, **options)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)
        self.assertNotIn('/declared', str(raised.exception))

    def integrity(self, values, **options):
        self.error('alden_source_body_integrity', values, **options)

    def test_import_signature_and_fixed_error_codes(self):
        signature = inspect.signature(files.original_utf8_window)
        self.assertEqual(list(signature.parameters), ['packet', 'record', 'record_pointer', 'original_files',
                                                      'offset_bytes', 'max_bytes', 'cancelled'])
        for name in list(signature.parameters)[1:]:
            self.assertEqual(signature.parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(signature.parameters['offset_bytes'].default, 0)
        self.assertEqual(signature.parameters['max_bytes'].default, 16000)
        codes = {'alden_source_body_unavailable', 'alden_source_body_capture_unavailable',
                 'alden_source_body_integrity', 'alden_source_body_format_unsupported',
                 'alden_source_body_encoding_unsupported', 'alden_source_body_cursor_invalid'}
        self.assertEqual(files.SourceBodyError.CODES, codes)
        for code in codes:
            error = files.SourceBodyError(code)
            self.assertIsInstance(error, ValueError)
            self.assertEqual(error.code, code)
            self.assertEqual(str(error), code)
        for value in ('PRIVATE_BODY', None, []):
            with self.assertRaisesRegex(ValueError, '^collection_source_body_error_code_invalid$'):
                files.SourceBodyError(value)

    def test_seven_fields_and_producer_record_with_distinct_key_order(self):
        values = fixture()
        packet, record, _ = values
        packet['files'][0]['record'] = dict(reversed(list(record.items())))
        result = self.call(values)
        self.assertEqual(set(result), {'encoding', 'offset_bytes', 'end_bytes', 'next_offset_bytes',
                                      'total_bytes', 'text', 'text_sha256'})
        self.assertEqual(result['encoding'], 'utf-8')
        self.assertEqual(result['text'].encode(), b'First line\r\nSecond line\n')
        self.assertEqual(result['text_sha256'], sha(result['text'].encode()))

    def test_ascii_and_korean_partial_receipts_reassemble_exact_original(self):
        for data in (b'A' * 20000, ('가' * 6000).encode(), ('x\n' * 300).encode()):
            with self.subTest(size=len(data)):
                values = fixture(data)
                before = copy.deepcopy(values)
                self.assertTrue(values[1]['parsed']['truncated'])
                self.assertFalse(values[2][0]['body_complete'])
                offset, parts = 0, []
                while True:
                    result = self.call(values, offset_bytes=offset)
                    piece = result['text'].encode()
                    self.assertEqual(piece, data[offset:result['end_bytes']])
                    self.assertLessEqual(len(piece), 16000)
                    parts.append(piece)
                    if result['next_offset_bytes'] is None:
                        break
                    self.assertGreater(result['next_offset_bytes'], offset)
                    offset = result['next_offset_bytes']
                self.assertEqual(b''.join(parts), data)
                self.assertEqual(sha(b''.join(parts)), values[1]['input_sha256'])
                self.assertEqual(values, before)

    def test_four_byte_scalar_boundary_and_cursor_errors(self):
        values = fixture(b'A' * 15999 + '😀'.encode() + b'Z')
        first = self.call(values)
        self.assertEqual(first['end_bytes'], 15999)
        self.assertEqual(first['next_offset_bytes'], 15999)
        second = self.call(values, offset_bytes=15999)
        self.assertEqual(second['text'], '😀Z')
        self.assertIsNone(second['next_offset_bytes'])
        for offset in (16000, 16001, 16002, 16005):
            self.error('alden_source_body_cursor_invalid', values, offset_bytes=offset)

    def test_minimum_budget_all_scalar_widths_and_preserved_bom_newlines_nfd(self):
        data = ('\ufeff가😀e\u0301\r\n\t  끝\r\n').encode()
        values = fixture(data)
        offset, actual = 0, b''
        while True:
            result = self.call(values, offset_bytes=offset, max_bytes=4)
            self.assertLessEqual(len(result['text'].encode()), 4)
            self.assertGreater(result['end_bytes'], offset)
            actual += result['text'].encode()
            if result['next_offset_bytes'] is None:
                break
            offset = result['next_offset_bytes']
        self.assertEqual(actual, data)

    def test_eof_and_exact_budget(self):
        for data in (b'1234', b'A' * 16000):
            values = fixture(data)
            self.assertIsNone(self.call(values)['next_offset_bytes'])
            result = self.call(values, offset_bytes=len(data))
            self.assertEqual(result, {'encoding': 'utf-8', 'offset_bytes': len(data), 'end_bytes': len(data),
                                      'next_offset_bytes': None, 'total_bytes': len(data), 'text': '',
                                      'text_sha256': sha(b'')})
            self.error('alden_source_body_cursor_invalid', values, offset_bytes=len(data) + 1)

    def test_every_small_mixed_scalar_cursor_against_codepoint_boundaries(self):
        text = 'Aé가😀e\u0301Z'
        data, boundaries = text.encode(), [0]
        for character in text:
            boundaries.append(boundaries[-1] + len(character.encode()))
        values = fixture(data)
        for budget in (4, 5, 6, 7, 16, 16000):
            for offset in range(len(data) + 2):
                with self.subTest(budget=budget, offset=offset):
                    if offset not in boundaries:
                        self.error('alden_source_body_cursor_invalid', values, offset_bytes=offset, max_bytes=budget)
                    else:
                        end = max(b for b in boundaries if offset <= b <= min(offset + budget, len(data)))
                        result = self.call(values, offset_bytes=offset, max_bytes=budget)
                        self.assertEqual(result['end_bytes'], end)
                        self.assertEqual(result['text'].encode(), data[offset:end])

    def test_invalid_request_args_precede_packet_access(self):
        for options in ({'offset_bytes': -1}, {'offset_bytes': 5242881}, {'offset_bytes': True},
                        {'offset_bytes': 0.0}, {'offset_bytes': '0'}, {'max_bytes': 3}, {'max_bytes': 16001},
                        {'max_bytes': False}, {'max_bytes': 4.0}, {'max_bytes': '4'}, {'cancelled': None}):
            with self.subTest(options=options):
                with self.assertRaisesRegex(ValueError, '^collection_source_body_request_invalid$'):
                    files.original_utf8_window(None, record=None, record_pointer=None, original_files=None, **options)

    def test_bad_packet_schema_kind_root_counts_and_pointer(self):
        changes = [lambda p:p.update(schema=True), lambda p:p.update(schema=2), lambda p:p.update(kind='other'),
                   lambda p:p.update(root='/other'), lambda p:p.update(files=[]), lambda p:p.update(files=[{}] * 65),
                   lambda p:p.update(blobs_base64={}), lambda p:p.update(blobs_base64={str(i):'' for i in range(65)}),
                   lambda p:p.update(blobs_base64={'not-a-digest': 'PRIVATE_BODY'})]
        for change in changes:
            values = fixture(); change(values[0]); self.integrity(values)
        for pointer in ('/files/00/record', '/files/+0/record', '/files/1/record', '/files/64/record',
                        '/files/0/../record', '/files/0/record/private', '/record', None, 0):
            self.integrity(fixture(), record_pointer=pointer)
        for root in ('relative', '/a/../b', '/a//b', '/a\x00b', '', None):
            values = fixture(); values[1]['source_root'] = root; values[0]['root'] = root
            self.integrity(values)

    def test_selected_record_must_equal_verified_record_and_entry(self):
        values = fixture(); values[0]['files'][0]['record'] = copy.deepcopy(values[1])
        values[0]['files'][0]['record']['input_bytes'] += 1
        self.integrity(values)
        for field, value in (('file_id', 'other'), ('path', 'different.md')):
            values = fixture(); values[1][field] = value; self.integrity(values)
        values = fixture(); values[0]['files'][0]['extra'] = 'PRIVATE_BODY'; self.integrity(values)

    def test_strict_embedded_manifest_json_hash_and_canonical_base64(self):
        raws = [b'{"schema":1,"schema":1,"files":[]}', b'{"schema":1,"files":[],"x":NaN}',
                b'{"schema":true,"files":[]}', b'[]', b'not JSON', b'{"schema":1,"files":{}}']
        for raw in raws:
            values = fixture(); set_manifest(values[0], [], raw=raw); self.integrity(values)
        values = fixture(); values[0]['manifest_sha256'] = 'f' * 64; self.integrity(values)
        values = fixture(); values[0]['manifest_base64'] += '\n'; self.integrity(values)
        values = fixture(); values[0]['manifest_base64'] = 'A' * (4 * ((files.MAX_MANIFEST_BYTES + 2) // 3) + 4)
        self.integrity(values)

    def test_duplicate_json_key_and_noncanonical_pad_bits_cannot_hide_behind_matching_hashes(self):
        values = fixture()
        declaration = json.dumps([values[0]['files'][0]['entry']], ensure_ascii=False).encode()
        set_manifest(values[0], [], raw=b'{"schema":1,"schema":1,"files":' + declaration + b'}')
        self.integrity(values)
        values = fixture(b'f')
        self.assertEqual(base64.b64decode('Zh==', validate=True), b'f')
        values[0]['blobs_base64'][values[1]['input_sha256']] = 'Zh=='
        self.integrity(values)
        values = fixture()
        raw = base64.b64decode(values[0]['manifest_base64'])
        raw += b' ' * ((1 - len(raw)) % 3)
        set_manifest(values[0], [], raw=raw)
        alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
        canonical = values[0]['manifest_base64']
        self.assertTrue(canonical.endswith('=='))
        noncanonical = canonical[:-3] + alphabet[alphabet.index(canonical[-3]) ^ 1] + '=='
        self.assertEqual(base64.b64decode(noncanonical, validate=True), raw)
        values[0]['manifest_base64'] = noncanonical
        self.integrity(values)

    def test_exact_manifest_byte_cap_and_one_byte_over(self):
        values = fixture()
        raw = base64.b64decode(values[0]['manifest_base64'])
        raw += b' ' * (files.MAX_MANIFEST_BYTES - len(raw))
        set_manifest(values[0], [], raw=raw)
        self.assertEqual(self.call(values)['text'], 'First line\r\nSecond line\n')
        set_manifest(values[0], [], raw=raw + b' ')
        self.integrity(values)

    def test_manifest_declarations_use_existing_producer_rules(self):
        changes = [('id', ''), ('id', 'bad/id'), ('id', '-bad'), ('id', 'x' * 129),
                   ('path', '../escape.md'), ('path', '/absolute.md'), ('path', 'a//b.md'),
                   ('path', './a.md'), ('path', 'a\\b.md'), ('path', 'a\nb.md'), ('path', '.'),
                   ('label', ''), ('label', True), ('label', 'x' * 1025), ('extra', 'PRIVATE_BODY')]
        for key, value in changes:
            values = fixture(); values[0]['files'][0]['entry'][key] = value
            set_manifest(values[0], [values[0]['files'][0]['entry']]); self.integrity(values)
        for key in ('id', 'path'):
            values = fixture(); add_sibling(values[0], values[2])
            values[0]['files'][1]['entry'][key] = values[0]['files'][0]['entry'][key]
            set_manifest(values[0], [row['entry'] for row in values[0]['files']]); self.integrity(values)
        values = fixture(); set_manifest(values[0], []); self.integrity(values)

    def test_parser_contract_and_existing_coverage_are_validated(self):
        for change in (lambda r:r.update(format='.pdf'),
                       lambda r:r['parsed'].update(processing_version='future-parser'),
                       lambda r:r['parsed'].update(parser='macos-textutil'),
                       lambda r:r['parsed']['scope'].update(kind='converted-lines')):
            values = fixture(); change(values[1])
            self.error('alden_source_body_format_unsupported', values)
        for change in (lambda r:r.update(schema=True), lambda r:r.update(input_bytes=True),
                       lambda r:r.update(parser_code_sha256='bad'), lambda r:r.update(format='.txt'),
                       lambda r:r['parsed']['scope'].update(body_complete='yes'),
                       lambda r:r['parsed'].update(text_sha256='f' * 64),
                       lambda r:r['parsed']['segments'][0].update(text='forged'),
                       lambda r:r['parsed'].update(truncated=True),
                       lambda r:r['parsed'].update(text='\ud800')):
            values = fixture(); change(values[1]); self.integrity(values)
        with patch.object(files, 'file_extraction_coverage', wraps=files.file_extraction_coverage) as coverage:
            self.call()
            coverage.assert_called_once()

    def test_original_receipt_must_match_selected_identity_sha_and_coverage(self):
        changes = [('file_id', 'other'), ('sha256', 'f' * 64), ('json_pointer', '/files/0/record'),
                   ('encoding', 'utf-8'), ('body_complete', False), ('body_complete', 1), ('extra', 'PRIVATE_BODY')]
        for key, value in changes:
            values = fixture(); values[2][0][key] = value; self.integrity(values)
        values = fixture(); add_sibling(values[0], values[2]); values[2][1] = dict(values[2][0]); self.integrity(values)
        values = fixture(); values[2].clear(); self.integrity(values)
        values = fixture(); values[0]['files'][0]['blob_sha256'] = 'f' * 64; self.integrity(values)

    def test_selected_base64_length_hash_and_maximum_file_budget(self):
        for base64_text in ('!!!!', 'YWJj\n', 'Zg== ', 'Zh==', 'é', ''):
            values = fixture(); values[0]['blobs_base64'][values[1]['input_sha256']] = base64_text
            self.integrity(values)
        values = fixture(); values[0]['blobs_base64'].clear(); self.integrity(values)
        values = fixture(); values[0]['blobs_base64'][values[1]['input_sha256']] = base64.b64encode(b'changed').decode()
        self.integrity(values)
        values = fixture(); values[1]['input_bytes'] += 1; self.integrity(values)
        values = fixture(); values[1]['input_bytes'] = files.MAX_FILE_BYTES + 1; self.integrity(values)
        values = fixture(); values[0]['blobs_base64'][values[1]['input_sha256']] = 'A' * (4 * ((files.MAX_FILE_BYTES + 2) // 3) + 4)
        self.integrity(values)

    def test_only_selected_blob_is_decoded_and_equal_bytes_keep_distinct_ids(self):
        values = fixture(); add_sibling(values[0], values[2])
        sibling_hash = values[0]['files'][1]['blob_sha256']
        values[0]['blobs_base64'][sibling_hash] = 'not base64; must not decode'
        with patch.object(files.base64, 'b64decode', wraps=base64.b64decode) as decoded:
            self.assertEqual(self.call(values)['text'], 'First line\r\nSecond line\n')
            self.assertEqual(decoded.call_count, 2)  # Embedded manifest and selected original only.
        values = fixture(); add_sibling(values[0], values[2], data=b'First line\r\nSecond line\n')
        self.assertEqual(len(values[0]['blobs_base64']), 1)
        for index in (0, 1):
            record = values[0]['files'][index]['record']
            result = files.original_utf8_window(values[0], record=record, record_pointer=f'/files/{index}/record', original_files=values[2])
            self.assertEqual(result['text'].encode(), b'First line\r\nSecond line\n')

    def test_sixty_four_files_and_final_canonical_pointer_are_supported(self):
        values = fixture()
        for index in range(1, 64):
            add_sibling(values[0], values[2], data=f'body-{index}'.encode(), path=f'item-{index}.txt', file_id=f'file-{index}')
        self.assertEqual(len(values[0]['blobs_base64']), 64)
        result = files.original_utf8_window(values[0], record=values[0]['files'][63]['record'],
                                            record_pointer='/files/63/record', original_files=values[2])
        self.assertEqual(result['text'], 'body-63')

    def test_original_formats_txt_and_uppercase_markdown_suffix(self):
        for path in ('plain.txt', 'folder/notes.MD'):
            values = fixture('한글 Markdown  \n'.encode(), path=path, file_id='File_1:part-2.3')
            self.assertEqual(self.call(values)['text'], '한글 Markdown  \n')

    def test_entire_original_encoding_and_controls_checked_before_any_window_success(self):
        for data in (b'good prefix' + b'\xff', b'good prefix\x00', b'good prefix\x7f',
                     'prefix\u0085'.encode(), 'UTF16 original'.encode('utf-16')):
            values = fixture(data, extracted='good prefix')
            self.error('alden_source_body_encoding_unsupported', values, max_bytes=4)
            self.error('alden_source_body_encoding_unsupported', values, offset_bytes=len(data), max_bytes=4)

    def test_cancellation_at_every_checkpoint_and_unexpected_abort_propagate(self):
        values = fixture(b'valid ' * 3000)
        checks = []
        self.call(values, cancelled=lambda: checks.append(True) or False)
        self.assertGreaterEqual(len(checks), 10)
        for stop in range(1, len(checks) + 1):
            calls = []
            def cancelled():
                calls.append(True)
                return len(calls) == stop
            with self.subTest(checkpoint=stop):
                with self.assertRaisesRegex(RuntimeError, '^collection_cancelled$'):
                    self.call(values, cancelled=cancelled)
                self.assertEqual(len(calls), stop)
        class RuntimeAbort(RuntimeError):
            pass
        def abort():
            raise RuntimeAbort('owned abort')
        with self.assertRaises(RuntimeAbort):
            self.call(values, cancelled=abort)

    def test_helper_has_no_file_parser_native_or_process_calls_and_does_not_mutate(self):
        values = fixture()
        before = encoded(values)
        with patch('builtins.open', side_effect=AssertionError('file IO')), \
             patch.object(files.os, 'open', side_effect=AssertionError('file IO')), \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('file IO')), \
             patch.object(files, '_manifest', side_effect=AssertionError('original manifest IO')), \
             patch.object(files, 'file_set_snapshot', side_effect=AssertionError('snapshot')), \
             patch.object(files, 'parse_file', side_effect=AssertionError('parser')), \
             patch.object(files, 'run_owned_json', side_effect=AssertionError('native process')), \
             patch.object(files.subprocess, 'Popen', side_effect=AssertionError('process')):
            self.call(values)
        self.assertEqual(encoded(values), before)

    def test_maximum_file_is_bounded_and_full_hash_verified_for_a_small_window(self):
        data = b'A' * files.MAX_FILE_BYTES
        values = fixture(data)
        result = self.call(values, max_bytes=4)
        self.assertEqual(result['text'], 'AAAA')
        self.assertEqual(result['total_bytes'], files.MAX_FILE_BYTES)
        self.assertEqual(result['next_offset_bytes'], 4)
        # Corrupt only the far tail: a small valid prefix must not conceal it.
        values[0]['blobs_base64'][values[1]['input_sha256']] = base64.b64encode(data[:-1] + b'B').decode()
        self.integrity(values, max_bytes=4)


if __name__ == '__main__':
    unittest.main()
