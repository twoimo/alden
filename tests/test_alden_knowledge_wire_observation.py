"""Offline wire evidence regressions through typed input and generated collections.

Only fixture construction/non-test helpers are reused from KnowledgeTurnTests;
none of its test methods are inherited or collected here. Transport, sockets and
subprocesses are guarded. These observations attest mocked HTTP decoding only.
"""
from __future__ import annotations

import copy
import http.client
import inspect
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import unittest
import urllib.request
import urllib.parse
from unittest.mock import patch
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import alden_chat as chat
import alden_chat_metrics as metrics
import alden_knowledge_turn as loop
import alden_knowledge_tool_protocol as protocol
from alden_chat_turns import get_request
from tests import test_alden_knowledge_turn as fixture

completion, MODEL = fixture.completion, fixture.MODEL


BASE = {'origin': 'local_http_completion', 'response_bytes': 123,
        'allow_tool': True, 'outer_state': 'object'}
ENUMS = {
    'choices_state': ('missing', 'invalid_type', 'list'),
    'first_choice_state': ('object', 'invalid_type'),
    'finish_reason': ('missing', 'null', 'invalid_type', 'unknown', 'stop',
                      'tool_calls', 'length', 'content_filter', 'function_call'),
    'message_state': ('missing', 'invalid_type', 'object'),
    'tool_calls_state': ('missing', 'null', 'invalid_type', 'list'),
}
BOOLS = ('choice_count_overflow', 'choice_index_valid',
         'tool_call_count_overflow', 'legacy_function_call_present')
COUNTS = ('choice_count', 'tool_call_count')
ALLOWED = set(BASE) | set(ENUMS) | set(BOOLS) | set(COUNTS)


def encoded(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False,
                      sort_keys=True, separators=(',', ':')).encode('utf-8')


def sanitized(rows, **extra_loop):
    return metrics.sanitize_generation(
        {'transport': 'local', 'model': MODEL, 'reasoning_effort': None},
        {'tool_loop': {'state': 'failed', 'hops': [], 'read_count': 0,
                       'wire_observations': rows, **extra_loop}},
        {'conversation_id': 'a' * 32, 'turn_id': 1, 'context_version': 1})


class _OfflineCase(unittest.TestCase):
    def setUp(self):
        # Install before any fixture setup; the one permitted HTTP seam is the
        # in-memory alden_voice._local_urlopen replacement in _Fixture.setUp.
        for target in (
            (socket.socket, 'connect'), (socket.socket, 'connect_ex'),
            (socket, 'create_connection'), (urllib.request, 'urlopen'),
            (http.client.HTTPConnection, 'connect'),
            (http.client.HTTPSConnection, 'connect'), (subprocess, 'Popen'),
        ):
            guard = patch.object(*target, side_effect=AssertionError('offline guard'))
            guard.start()
            self.addCleanup(guard.stop)


class WireProducerTests(_OfflineCase):
    def observe(self, value, *, allow_tool=True):
        producer = getattr(metrics, 'observe_local_knowledge_completion', None)
        self.assertTrue(callable(producer), 'wire observation producer absent')
        original = copy.deepcopy(value)
        result = producer(value, response_bytes=123, allow_tool=allow_tool)
        self.assertEqual(value, original, 'producer mutated decoded HTTP payload')
        self.assertEqual({k: result[k] for k in BASE}, {**BASE, 'allow_tool': allow_tool,
                         'outer_state': 'object' if type(value) is dict else 'invalid_type'})
        self.assertFalse(result.keys() - ALLOWED, result)
        return result

    def test_outer_and_progressive_states(self):
        for value in (None, [], 'private-outer', 4, False):
            with self.subTest(outer=type(value).__name__):
                self.assertEqual(self.observe(value), {**BASE, 'outer_state': 'invalid_type'})
        self.assertEqual(self.observe({}), {**BASE, 'choices_state': 'missing'})
        for choices in (None, {}, 'private-choices', True, 7):
            with self.subTest(choices=type(choices).__name__):
                row = self.observe({'choices': choices})
                self.assertEqual(row['choices_state'], 'invalid_type')
                self.assertNotIn('choice_count', row)
        empty = self.observe({'choices': []})
        self.assertEqual(empty['choice_count'], 0)
        self.assertFalse(empty['choice_count_overflow'])
        self.assertNotIn('first_choice_state', empty)
        for first in (None, [], 'private-first', 0, False):
            with self.subTest(first=type(first).__name__):
                row = self.observe({'choices': [first]})
                self.assertEqual(row['first_choice_state'], 'invalid_type')
                self.assertNotIn('finish_reason', row)

    def test_finish_closed_vocabulary(self):
        for reason in ('stop', 'tool_calls', 'length', 'content_filter', 'function_call'):
            with self.subTest(reason=reason):
                self.assertEqual(self.observe({'choices': [{'finish_reason': reason}]})
                                 ['finish_reason'], reason)
        for supplied, expected in ((None, 'null'), ('PRIVATE:arbitrary-finish', 'unknown'),
                                   ([], 'invalid_type'), ({}, 'invalid_type'),
                                   (True, 'invalid_type'), (0, 'invalid_type')):
            with self.subTest(expected=expected, supplied=type(supplied).__name__):
                row = self.observe({'choices': [{'finish_reason': supplied}]})
                self.assertEqual(row['finish_reason'], expected)
                self.assertNotIn('PRIVATE', json.dumps(row))
        self.assertEqual(self.observe({'choices': [{}]})['finish_reason'], 'missing')

    def test_message_tool_calls_and_legacy_presence(self):
        self.assertEqual(self.observe({'choices': [{}]})['message_state'], 'missing')
        for message in (None, [], 'PRIVATE:message', 1, False):
            with self.subTest(message=type(message).__name__):
                row = self.observe({'choices': [{'message': message}]})
                self.assertEqual(row['message_state'], 'invalid_type')
                self.assertNotIn('tool_calls_state', row)
        row = self.observe({'choices': [{'message': {}}]})
        self.assertEqual(row['tool_calls_state'], 'missing')
        self.assertFalse(row['legacy_function_call_present'])
        for calls, state in ((None, 'null'), ({}, 'invalid_type'),
                             ('PRIVATE:calls', 'invalid_type'), (False, 'invalid_type'),
                             (0, 'invalid_type'), ([], 'list')):
            with self.subTest(calls=type(calls).__name__):
                row = self.observe({'choices': [{'message': {'tool_calls': calls}}]})
                self.assertEqual(row['tool_calls_state'], state)
                self.assertEqual('tool_call_count' in row, type(calls) is list)
        for legacy in (None, {}, 'PRIVATE:legacy'):
            with self.subTest(legacy=type(legacy).__name__):
                row = self.observe({'choices': [{'message': {'function_call': legacy}}]})
                self.assertTrue(row['legacy_function_call_present'])

    def test_index_zero_requires_int_not_bool_or_numeric_lookalike(self):
        for index, valid in ((0, True), (False, False), (True, False),
                             (0.0, False), ('0', False), (1, False), (-1, False), (None, False)):
            with self.subTest(index=repr(index)):
                row = self.observe({'choices': [{'index': index}]})
                self.assertIs(row['choice_index_valid'], valid)
        self.assertIs(self.observe({'choices': [{}]})['choice_index_valid'], False)

    def test_counts_are_list_lengths_clipped_at_64(self):
        for count in (0, 1, 2, 63, 64, 65, 129):
            with self.subTest(count=count):
                value = {'choices': [{'message': {'tool_calls': [None] * count}}] * count}
                row = self.observe(value)
                self.assertEqual(row['choice_count'], min(count, 64))
                self.assertIs(type(row['choice_count']), int)
                self.assertIs(row['choice_count_overflow'], count > 64)
                if count:
                    self.assertEqual(row['tool_call_count'], min(count, 64))
                    self.assertIs(row['tool_call_count_overflow'], count > 64)

    def test_private_content_ids_arguments_and_model_never_enter_observation(self):
        value = completion()
        value.update(id='PRIVATE:response-id', model='PRIVATE:model')
        choice = value['choices'][0]
        choice['finish_reason'] = 'PRIVATE:finish'
        choice['message'].update(content='PRIVATE:preface', reasoning_content='PRIVATE:reasoning')
        choice['message']['tool_calls'][0].update(id='PRIVATE:call-id')
        choice['message']['tool_calls'][0]['function']['arguments'] = 'PRIVATE:arguments'
        row = self.observe(value, allow_tool=False)
        self.assertNotIn('PRIVATE', json.dumps(row))
        self.assertEqual(row['finish_reason'], 'unknown')
        self.assertEqual(row['tool_call_count'], 1)

    def test_producer_returns_fresh_rows(self):
        value = completion()
        one, two = self.observe(value), self.observe(value)
        self.assertEqual(one, two)
        self.assertIsNot(one, two)
        one['choice_count'] = 63
        value['choices'][0]['message']['tool_calls'].clear()
        self.assertEqual(two['choice_count'], 1)
        self.assertEqual(two['tool_call_count'], 1)

    def test_protocol_parse_completion_api_is_unchanged(self):
        signature = inspect.signature(protocol.parse_completion)
        self.assertEqual(list(signature.parameters), ['payload', 'expected_model', 'allow_tool'])
        self.assertEqual(signature.parameters['allow_tool'].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIs(signature.parameters['allow_tool'].default, True)
        self.assertEqual(protocol.parse_completion(completion(), MODEL)['kind'], 'tool')
        self.assertEqual(protocol.parse_completion(completion('final'), MODEL)['kind'], 'final')
        with self.assertRaises(protocol.KnowledgeToolProtocolError):
            protocol.parse_completion(completion(), MODEL, allow_tool=False)


class WireSanitizerTests(_OfflineCase):
    def rows(self, source):
        safe = sanitized(source)
        self.assertIn('wire_observations', safe['tool_loop'], 'wire sanitizer discarded observations')
        return safe['tool_loop']['wire_observations']

    def test_empty_one_two_rows_and_fresh_nested_copies(self):
        self.assertEqual(self.rows([]), [])
        raw = [dict(BASE), {**BASE, 'allow_tool': False}]
        self.assertEqual(self.rows(raw[:1]), raw[:1])
        safe = self.rows(raw)
        self.assertEqual(safe, raw)
        self.assertIsNot(safe, raw)
        self.assertIsNot(safe[0], raw[0])
        self.assertIsNot(safe[1], raw[1])
        raw[0]['response_bytes'] = 0
        safe[1]['allow_tool'] = True
        self.assertEqual(safe[0]['response_bytes'], 123)
        self.assertFalse(raw[1]['allow_tool'])

    def test_more_than_two_or_nonlist_rows_rejected(self):
        for raw in ([dict(BASE)] * 3, None, {}, (), True, 'PRIVATE:list'):
            with self.subTest(raw=type(raw).__name__), self.assertRaisesRegex(
                    ValueError, '^generation_metadata_invalid$'):
                sanitized(raw)

    def test_required_bases_missing_or_invalid_rejected(self):
        for key in BASE:
            raw = dict(BASE)
            del raw[key]
            with self.subTest(missing=key), self.assertRaisesRegex(
                    ValueError, '^generation_metadata_invalid$'):
                sanitized([raw])
        invalid = {
            'origin': (None, 'sdk', [], 'PRIVATE:origin'),
            'response_bytes': (None, -1, 65537, True, False, 1.0, '123'),
            'allow_tool': (None, 0, 1, 'true', []),
            'outer_state': (None, 'dict', [], 'PRIVATE:outer'),
        }
        for key, values in invalid.items():
            for value in values:
                with self.subTest(key=key, value=repr(value)), self.assertRaisesRegex(
                        ValueError, '^generation_metadata_invalid$'):
                    sanitized([{**BASE, key: value}])
        for raw in (None, [], True, 'PRIVATE:row'):
            with self.subTest(row=type(raw).__name__), self.assertRaisesRegex(
                    ValueError, '^generation_metadata_invalid$'):
                sanitized([raw])

    def test_required_base_boundaries_and_invalid_outer_are_valid(self):
        for size in (0, 65536):
            for allow_tool in (False, True):
                for outer in ('object', 'invalid_type'):
                    with self.subTest(size=size, allow_tool=allow_tool, outer=outer):
                        row = {**BASE, 'response_bytes': size, 'allow_tool': allow_tool,
                               'outer_state': outer}
                        self.assertEqual(self.rows([row]), [row])

    def test_closed_optional_enums_booleans_counts_preserved(self):
        for key, values in ENUMS.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.rows([{**BASE, key: value}]), [{**BASE, key: value}])
        for key in BOOLS:
            for value in (False, True):
                with self.subTest(key=key, value=value):
                    self.assertIs(self.rows([{**BASE, key: value}])[0][key], value)
        for key in COUNTS:
            for value in (0, 1, 64):
                with self.subTest(key=key, value=value):
                    row = self.rows([{**BASE, key: value}])[0]
                    self.assertEqual(row[key], value)
                    self.assertIs(type(row[key]), int)

    def test_malformed_optional_values_and_unknown_private_fields_discarded(self):
        for key in ENUMS:
            for value in (None, [], {}, False, 1, 'PRIVATE:enum'):
                with self.subTest(key=key, value=repr(value)):
                    self.assertEqual(self.rows([{**BASE, key: value}]), [BASE])
        for key in BOOLS:
            for value in (None, 0, 1, 'true', [], {}):
                with self.subTest(key=key, value=repr(value)):
                    self.assertEqual(self.rows([{**BASE, key: value}]), [BASE])
        for key in COUNTS:
            for value in (None, False, True, -1, 65, 1.0, '1', [], {}):
                with self.subTest(key=key, value=repr(value)):
                    self.assertEqual(self.rows([{**BASE, key: value}]), [BASE])
        raw = {**BASE, 'content': 'PRIVATE:content', 'preface': 'PRIVATE:preface',
               'arguments': {'PRIVATE:arguments': []}, 'id': 'PRIVATE:id',
               'model': 'PRIVATE:model', 'other': 'PRIVATE:other'}
        self.assertEqual(self.rows([raw]), [BASE])

    def worst_twohop_fixture(self, *, query_trace=False):
        # Maximum legal lengths/counters and every usage detail on both hops,
        # plus real producer rows for a valid tool+final exchange. No fabricated
        # impossible failed two-tool transcript is used to inflate the budget.
        maximum = 9_007_199_254_740_991
        model = 'm' * 128
        usage = {key: maximum for key in (
            'input_tokens', 'output_tokens', 'total_tokens', 'prompt_tokens', 'completion_tokens')}
        usage.update({
            'input_tokens_details': {'cached_tokens': maximum},
            'output_tokens_details': {'reasoning_tokens': maximum},
            'prompt_tokens_details': {'cached_tokens': maximum},
            'completion_tokens_details': {'reasoning_tokens': maximum,
                'accepted_prediction_tokens': maximum, 'rejected_prediction_tokens': maximum}})
        producer = getattr(metrics, 'observe_local_knowledge_completion', None)
        self.assertTrue(callable(producer), 'wire observation producer absent')
        observations = [producer(completion(kind), response_bytes=65536, allow_tool=allow)
                        for kind, allow in (('tool', True), ('final', False))]
        selected = {'transport': 'local', 'model': model, 'reasoning_effort': 'ultra'}
        ticket = {'conversation_id': 'a' * 32, 'turn_id': maximum, 'context_version': maximum,
                  'source': 'text'}
        raw = {'request': {**ticket, 'transport': 'local', 'model_id': model,
                           'reasoning_effort': 'ultra', 'state': 'completed', 'cancelled': False},
               'backend_request_id': 'z' * 128, 'response_model_id': model, 'usage': usage,
               'tool_loop': {'state': 'read_completed', 'read_count': 1,
                   'elapsed_ms': 86400000, 'read': {'state': 'ambiguous_room',
                       'mode': 'm' * 32, 'sources': maximum},
                   'hops': [{'id': key * 128, 'model': model, 'kind': kind, 'usage': usage}
                            for key, kind in (('y', 'tool'), ('z', 'final'))],
                   'wire_observations': observations}}
        if query_trace:
            from alden_scoped_knowledge_query import scoped_collection_query
            trace = scoped_collection_query('fixture 저장된 자료에서 needle 찾아줘',
                                            projects=['fixture'])['trace']
            raw['retrieval'] = {'query_trace': trace}
        return selected, ticket, raw, observations

    def test_worst_legitimate_twohop_fits_generation_and_failure_caps(self):
        selected, ticket, raw, observations = self.worst_twohop_fixture()
        result = metrics.sanitize_generation(selected, raw, ticket)
        self.assertEqual(result['tool_loop']['wire_observations'], observations)
        self.assertNotIn('wire_observations_compacted', result['tool_loop'])
        self.assertLessEqual(len(encoded(result)), metrics.MAX_GENERATION_JSON_BYTES)
        receipt = chat.generation_evidence(selected, raw, ticket, 'completed')
        self.assertLessEqual(len(encoded(receipt)), 4096)
        raw['request']['state'] = raw['tool_loop']['state'] = 'failed'
        raw['error_code'] = 'local_llm_tool_protocol_invalid'
        failed = chat.failure_generation(selected, {**ticket, 'adapter_metrics': raw}, 'failed')
        # Even without a query trace this extreme failed receipt now reaches
        # the reserved terminal-state budget and legitimately needs compaction.
        self.assert_compacted_preserves_evidence(failed, selected, ticket, raw)
        self.assertLessEqual(len(encoded(failed)), 4096)

    def assert_compacted_preserves_evidence(self, result, selected, ticket, raw):
        self.assertEqual(metrics.MAX_GENERATION_JSON_BYTES, 4096)
        self.assertLessEqual(len(encoded(result)), 4096)
        self.assertEqual(result['selection'], {'transport': 'local', 'model_id': selected['model'],
                                              'reasoning_effort': selected['reasoning_effort']})
        self.assertEqual(result['request'], {
            **{key: ticket[key] for key in ('conversation_id', 'turn_id', 'context_version')},
            'context_binding': 'verified', 'transport': 'local',
            'model_id': raw['request']['model_id'], 'reasoning_effort': 'ultra',
            'state': raw['request']['state'], 'cancelled': False})
        self.assertEqual(result['response'], {'model_id': raw['response_model_id'],
            'id': raw['backend_request_id'], 'usage': raw['usage']})
        if 'retrieval' in raw:
            self.assertEqual(result['knowledge_query'], raw['retrieval']['query_trace'])
        else:
            self.assertNotIn('knowledge_query', result)
        safe_loop = result['tool_loop']
        self.assertIs(safe_loop['wire_observations_compacted'], True)
        self.assertEqual(safe_loop['state'], raw['tool_loop']['state'])
        self.assertEqual(safe_loop['read_count'], raw['tool_loop']['read_count'])
        self.assertEqual(safe_loop['read'], raw['tool_loop']['read'])
        self.assertEqual(safe_loop['hops'], [{'id': row['id'], 'model_id': row['model'],
            'kind': row['kind'], 'usage': row['usage']} for row in raw['tool_loop']['hops']])
        rows, originals = safe_loop['wire_observations'], raw['tool_loop']['wire_observations']
        self.assertEqual(len(rows), len(originals))
        self.assertLessEqual(len(rows), 2)
        self.assertIsNot(rows, originals)
        optional = {'first_choice_state', 'choice_index_valid', 'message_state',
                    'legacy_function_call_present', 'choices_state', 'tool_calls_state'}
        protected = set(BASE) | {'finish_reason'} | set(COUNTS) | set(BOOLS)
        for row, original in zip(rows, originals):
            self.assertIsNot(row, original)
            self.assertTrue(row.keys() <= original.keys(), 'compaction invented a wire field')
            removed = original.keys() - row.keys()
            self.assertTrue(removed <= optional, removed)
            for key in protected - {'legacy_function_call_present', 'choice_index_valid'}:
                self.assertEqual(key in row, key in original, key)
                if key in original:
                    self.assertEqual(row[key], original[key], key)
            if original.get('legacy_function_call_present') is True:
                self.assertIs(row['legacy_function_call_present'], True)
            for key in row:
                self.assertEqual(row[key], original[key], key)

    def test_stressed_query_trace_sanitization_compacts_without_mutating_inputs(self):
        selected, ticket, raw, _ = self.worst_twohop_fixture(query_trace=True)
        before = copy.deepcopy((selected, ticket, raw))
        result = metrics.sanitize_generation(selected, raw, ticket)
        self.assert_compacted_preserves_evidence(result, selected, ticket, raw)
        self.assertLessEqual(len(encoded(result)), 4096 - 32)
        self.assertEqual((selected, ticket, raw), before)
        original_count = raw['tool_loop']['wire_observations'][0]['choice_count']
        result['tool_loop']['wire_observations'][0]['choice_count'] = 0
        again = metrics.sanitize_generation(selected, raw, ticket)
        self.assertEqual(again['tool_loop']['wire_observations'][0]['choice_count'], original_count)
        self.assertEqual((selected, ticket, raw), before)

    def test_stressed_completed_receipt_retains_primary_metrics_and_query_trace(self):
        selected, ticket, raw, _ = self.worst_twohop_fixture(query_trace=True)
        before = copy.deepcopy((selected, ticket, raw))
        result = chat.generation_evidence(selected, raw, ticket, 'completed')
        self.assertEqual(result['terminal_state'], 'completed')
        self.assert_compacted_preserves_evidence(result, selected, ticket, raw)
        self.assertEqual((selected, ticket, raw), before)

    def test_stressed_failure_and_superseded_receipts_do_not_fall_back(self):
        for terminal in ('failed', 'superseded'):
            with self.subTest(terminal=terminal):
                selected, ticket, raw, _ = self.worst_twohop_fixture(query_trace=True)
                raw['request']['state'] = raw['tool_loop']['state'] = 'failed'
                raw['error_code'] = 'local_llm_tool_protocol_invalid'
                before = copy.deepcopy((selected, ticket, raw))
                direct = chat.generation_evidence(selected, raw, ticket, terminal)
                failed = chat.failure_generation(selected, {**ticket, 'adapter_metrics': raw},
                                                  terminal, error_code=raw['error_code'])
                self.assertEqual(failed, direct)
                self.assertEqual(failed['terminal_state'], terminal)
                self.assertEqual(failed['error_code'], 'local_llm_tool_protocol_invalid')
                self.assert_compacted_preserves_evidence(failed, selected, ticket, raw)
                self.assertEqual((selected, ticket, raw), before)

    def test_compaction_keeps_finish_counts_overflow_true_legacy_and_unknowns(self):
        # Pure sanitizer stress rows need not be successful protocol replies.
        # They describe rejection shapes; compaction must preserve those facts.
        producer = metrics.observe_local_knowledge_completion
        secret = 'PRIVATE:compaction:never-retain'
        first = {'choices': [{'index': False, 'finish_reason': 'length',
            'message': {'content': secret, 'function_call': None, 'tool_calls': []}}] * 65}
        second = {'model': secret, 'id': secret, 'choices': [{'index': 0,
            'finish_reason': 'tool_calls', 'message': {'content': secret,
                'tool_calls': [{'id': secret, 'arguments': secret}] * 65}}]}
        rows = [producer(first, response_bytes=65536, allow_tool=True),
                producer(second, response_bytes=65536, allow_tool=False)]
        for variant in ('counts', 'unknown_fields_absent'):
            with self.subTest(variant=variant):
                selected, ticket, raw, _ = self.worst_twohop_fixture(query_trace=True)
                # Leave enough mandatory budget for both observed count groups
                # and true legacy presence while still requiring compaction.
                model = 'm' * 112
                selected['model'] = raw['request']['model_id'] = raw['response_model_id'] = model
                for hop in raw['tool_loop']['hops']:
                    hop['model'] = model
                raw['tool_loop']['wire_observations'] = copy.deepcopy(rows)
                if variant == 'unknown_fields_absent':
                    # Missing facts must stay absent, even while a different
                    # row's observed list count/overflow survives compaction.
                    for key in ('tool_call_count', 'tool_call_count_overflow'):
                        del raw['tool_loop']['wire_observations'][1][key]
                before = copy.deepcopy(raw)
                result = metrics.sanitize_generation(selected, raw, ticket)
                self.assert_compacted_preserves_evidence(result, selected, ticket, raw)
                self.assertNotIn(secret, json.dumps(result))
                safe_rows = result['tool_loop']['wire_observations']
                self.assertEqual(safe_rows[0]['choice_count'], 64)
                self.assertIs(safe_rows[0]['choice_count_overflow'], True)
                self.assertEqual(safe_rows[0]['tool_call_count'], 0)
                self.assertIs(safe_rows[0]['legacy_function_call_present'], True)
                self.assertEqual(safe_rows[0]['finish_reason'], 'length')
                if variant == 'counts':
                    self.assertEqual(safe_rows[1]['tool_call_count'], 64)
                    self.assertIs(safe_rows[1]['tool_call_count_overflow'], True)
                else:
                    self.assertNotIn('tool_call_count', safe_rows[1])
                    self.assertNotIn('tool_call_count_overflow', safe_rows[1])
                self.assertEqual(raw, before)

    def test_terminal_reserve_compacts_a_record_that_fits_the_raw_4096_cap(self):
        selected, ticket, raw, _ = self.worst_twohop_fixture(query_trace=True)
        model = 'm' * 89
        selected['model'] = raw['request']['model_id'] = raw['response_model_id'] = model
        for hop in raw['tool_loop']['hops']:
            hop['model'] = model
        # Same maximum fixture is 4277 bytes at model length 128. Five model
        # copies become 39 bytes shorter: 4082 fits 4096 but not the 32 reserve.
        self.assertTrue(4096 - 32 < 4277 - 5 * (128 - len(model)) <= 4096)
        before = copy.deepcopy(raw)
        result = metrics.sanitize_generation(selected, raw, ticket)
        self.assert_compacted_preserves_evidence(result, selected, ticket, raw)
        self.assertLessEqual(len(encoded(result)), 4096 - 32)
        self.assertLessEqual(len(encoded(chat.generation_evidence(selected, raw, ticket,
                                                                  'superseded'))), 4096)
        self.assertEqual(raw, before)

    def test_compaction_marker_is_host_derived_and_normal_rows_are_unchanged(self):
        rows = [dict(BASE), {**BASE, 'allow_tool': False}]
        for supplied in (None, False, True, 'PRIVATE:marker'):
            with self.subTest(supplied=supplied):
                result = sanitized(rows, wire_observations_compacted=supplied)
                self.assertEqual(result['tool_loop']['wire_observations'], rows)
                self.assertNotIn('wire_observations_compacted', result['tool_loop'])

    def test_large_primary_metadata_omits_diagnostic_rows_without_mutating_input(self):
        selected, ticket, raw, _ = self.worst_twohop_fixture(query_trace=True)
        raw.update(elapsed_seconds=86400, first_visible_token_seconds=86400)
        raw['tool_loop']['wire_observations'] = [{**BASE, 'response_bytes': 65536,
            'allow_tool': allow, 'finish_reason': 'content_filter',
            'choice_count': 64, 'choice_count_overflow': True,
            'tool_call_count': 64, 'tool_call_count_overflow': True,
            'legacy_function_call_present': True} for allow in (True, False)]
        before = copy.deepcopy((selected, ticket, raw))
        result = metrics.sanitize_generation(selected, raw, ticket)
        self.assertEqual(len(result['tool_loop']['wire_observations']), 1)
        self.assertIs(result['tool_loop']['wire_observations'][0]['allow_tool'], False)
        self.assertIs(result['tool_loop']['wire_observations'][0]['legacy_function_call_present'], True)
        self.assertIs(result['tool_loop']['wire_observations_omitted_for_budget'], True)
        without = copy.deepcopy(raw)
        del without['tool_loop']['wire_observations']
        expected = metrics.sanitize_generation(selected, without, ticket)
        self.assert_final_diagnostics_only(result, expected, raw)
        self.assertLessEqual(len(encoded(result)), 4096 - 32)
        self.assertEqual((selected, ticket, raw), before)

    def empty_image_context(self, ticket):
        # Use the real selector for the context added even on empty typed input.
        with TemporaryDirectory(prefix='alden-wire-empty-image-') as root:
            self.assertEqual(chat.select_image_context(Path(root), ticket, [], []), [])
        return ticket['image_context']

    def assert_final_diagnostics_only(self, result, legacy, raw):
        self.assertLessEqual(len(encoded(result)), 4096)
        actual = copy.deepcopy(result)
        rows = actual['tool_loop'].pop('wire_observations', None)
        compacted = actual['tool_loop'].pop('wire_observations_compacted', None)
        omitted = actual['tool_loop'].pop('wire_observations_omitted_for_budget', None)
        self.assertEqual(actual, legacy, 'budget fitting changed primary or context evidence')
        originals = raw['tool_loop']['wire_observations']
        if rows is None:
            self.assertIsNone(compacted)
            if omitted is not None:
                self.assertIs(omitted, True)
        else:
            self.assertTrue(rows, 'an omitted diagnostic is unknown, not a zero-row observation')
            self.assertLessEqual(len(rows), len(originals))
            if len(rows) < len(originals):
                self.assertIs(omitted, True)
            # Retained records must be a suffix: latest HTTP observation wins.
            for row, source in zip(rows, originals[-len(rows):]):
                self.assertTrue(row.keys() <= source.keys())
                for key in row:
                    self.assertEqual(row[key], source[key])
                for key in set(BASE) | {'finish_reason'} | set(COUNTS) | {
                        'choice_count_overflow', 'tool_call_count_overflow'}:
                    self.assertEqual(key in row, key in source)
                    if key in source:
                        self.assertEqual(row[key], source[key])
                if source.get('legacy_function_call_present') is True:
                    self.assertIs(row['legacy_function_call_present'], True)

    def test_real_empty_image_context_and_query_trace_fit_completed_and_late_failures(self):
        for terminal in ('completed', 'failed', 'superseded'):
            with self.subTest(terminal=terminal):
                selected, ticket, raw, _ = self.worst_twohop_fixture(query_trace=True)
                self.empty_image_context(ticket)
                if terminal != 'completed':
                    raw['request']['state'] = raw['tool_loop']['state'] = 'failed'
                    raw['error_code'] = 'local_llm_tool_protocol_invalid'
                before = copy.deepcopy((selected, ticket, raw))
                without = copy.deepcopy(raw)
                del without['tool_loop']['wire_observations']
                legacy = chat.generation_evidence(selected, without, ticket, terminal)
                result = chat.generation_evidence(selected, raw, ticket, terminal)
                self.assertEqual(result['image_context'], ticket['image_context'])
                self.assert_final_diagnostics_only(result, legacy, raw)
                self.assertLess(len(result['tool_loop'].get('wire_observations', [])), 2)
                if terminal != 'completed':
                    saved = chat.failure_generation(selected, {**ticket, 'adapter_metrics': raw}, terminal)
                    self.assertEqual(saved, result, 'late context budget caused failure fallback')
                self.assertEqual((selected, ticket, raw), before)

    def test_late_document_and_link_receipts_preserve_legacy_primary_budget(self):
        selected, ticket, raw, _ = self.worst_twohop_fixture(query_trace=True)
        model = 'm' * 96
        selected['model'] = raw['request']['model_id'] = raw['response_model_id'] = model
        for hop in raw['tool_loop']['hops']:
            hop['model'] = model
        self.empty_image_context(ticket)
        ticket['document_context'] = {'selected': [], 'omitted': {'unavailable': 0},
                                     'history_scope': 'generated empty file context'}
        ticket['link_context'] = {'selected': [], 'omitted': {'capacity': 0},
                                  'history_scope': 'generated empty link context'}
        raw['request']['state'] = raw['tool_loop']['state'] = 'failed'
        raw['error_code'] = 'local_llm_tool_protocol_invalid'
        before = copy.deepcopy((selected, ticket, raw))
        without = copy.deepcopy(raw)
        del without['tool_loop']['wire_observations']
        legacy = chat.generation_evidence(selected, without, ticket, 'failed')
        result = chat.failure_generation(selected, {**ticket, 'adapter_metrics': raw}, 'failed')
        self.assertIn('document_context', result)
        self.assertIn('link_context', result)
        self.assertNotIn('wire_observations', result['tool_loop'])
        self.assert_final_diagnostics_only(result, legacy, raw)
        self.assertEqual((selected, ticket, raw), before)

    def test_exact_budget_removes_optional_marker_after_all_rows_are_omitted(self):
        # Isolate the final fitter with an already sanitized envelope. Opaque
        # generated context bytes model late caller extensions, not wire data.
        primary = sanitized([])
        del primary['tool_loop']['wire_observations']
        primary['context'] = ''
        primary['context'] = 'x' * (4096 - len(encoded(primary)))
        self.assertEqual(len(encoded(primary)), 4096)
        fitted = copy.deepcopy(primary)
        fitted['tool_loop']['wire_observations'] = [
            {**BASE, 'finish_reason': 'tool_calls'},
            {**BASE, 'allow_tool': False, 'finish_reason': 'stop'}]
        metrics._fit_wire_observations(fitted, reserve=0)
        self.assertEqual(fitted, primary)
        self.assertNotIn('wire_observations', fitted['tool_loop'])
        self.assertNotIn('wire_observations_omitted_for_budget', fitted['tool_loop'])
        # A prior early omission marker can itself stop fitting after the late
        # context extension. With no rows left, the final call must remove it.
        fitted['tool_loop']['wire_observations_omitted_for_budget'] = True
        metrics._fit_wire_observations(fitted, reserve=0)
        self.assertEqual(fitted, primary)

    def test_late_failure_error_code_is_fitted_before_durable_serialization(self):
        selected, ticket, raw, _ = self.worst_twohop_fixture(query_trace=True)
        model = 'm' * 74
        selected['model'] = raw['request']['model_id'] = raw['response_model_id'] = model
        for hop in raw['tool_loop']['hops']:
            hop['model'] = model
        self.empty_image_context(ticket)
        ticket['document_context'] = {'selected': [], 'omitted': {'unavailable': 0},
                                     'history_scope': 'generated empty file context'}
        ticket['link_context'] = {'selected': [], 'omitted': {'capacity': 0},
                                  'history_scope': 'generated empty link context'}
        # The adapter succeeded. A later host authority failure supplies its
        # safe error only after generation_evidence has added/fitted contexts.
        self.assertNotIn('error_code', raw)
        before = copy.deepcopy((selected, ticket, raw))
        without = copy.deepcopy(raw)
        del without['tool_loop']['wire_observations']
        code = 'local_knowledge_source_changed'
        legacy = chat.failure_generation(selected, {**ticket, 'adapter_metrics': without},
                                         'failed', error_code=code)
        result = chat.failure_generation(selected, {**ticket, 'adapter_metrics': raw},
                                         'failed', error_code=code)
        self.assertEqual(result['error_code'], code)
        self.assertIn('tool_loop', result, 'late error triggered a metadata-invalid fallback')
        self.assert_final_diagnostics_only(result, legacy, raw)
        from alden_chat_turns import _generation
        self.assertEqual(json.loads(_generation(result)), result)
        self.assertEqual((selected, ticket, raw), before)

    def test_preexisting_oversized_context_still_fails_closed(self):
        selected, ticket, raw, _ = self.worst_twohop_fixture(query_trace=True)
        context = self.empty_image_context(ticket)
        context['selected'] = [{'source_request_id': 'b' * 32, 'source_turn_id': i,
            'sha256': str(i) * 64, 'mime': 'image/png', 'size': 1, 'origin': 'previous'}
            for i in range(1, 5)]
        context['submitted_bytes'] = 4
        before = copy.deepcopy((selected, ticket, raw))
        without = copy.deepcopy(raw)
        del without['tool_loop']['wire_observations']
        for source in (without, raw):
            with self.subTest(with_wire='wire_observations' in source['tool_loop']), \
                    self.assertRaisesRegex(ValueError, '^generation_metadata_invalid$'):
                chat.generation_evidence(selected, source, ticket, 'completed')
        self.assertEqual((selected, ticket, raw), before)


class WireTypedInputTests(_OfflineCase):
    # Reuse helpers explicitly, without inheriting _Fixture/test_* methods.
    lease = fixture.KnowledgeTurnTests.lease
    run_input = fixture.KnowledgeTurnTests.run_input
    assert_failed = fixture.KnowledgeTurnTests.assert_failed

    def setUp(self):
        super().setUp()
        self.raw_responses = []
        connect = sqlite3.connect

        def generated_database_only(database, *args, **kwargs):
            value = str(database)
            if value.startswith('file:'):
                uri = urllib.parse.urlsplit(value)
                self.assertFalse(uri.netloc, 'nonlocal database URI')
                value = urllib.parse.unquote(uri.path)
            path = Path(value).resolve()
            self.assertTrue(path.is_relative_to(self.root), 'database outside owned fixture')
            return connect(database, *args, **kwargs)

        database_guard = patch.object(sqlite3, 'connect', side_effect=generated_database_only)
        database_guard.start()
        self.addCleanup(database_guard.stop)
        fixture.KnowledgeTurnTests.setUp(self)
        self.private_wire = 'PRIVATE:wire:' + uuid.uuid4().hex

    def urlopen(self, request, *, timeout):
        response = fixture.KnowledgeTurnTests.urlopen(self, request, timeout=timeout)
        self.raw_responses.append(response.getvalue())
        return response

    def prepare(self, payload, *, mime='application/json'):
        self.value = {**self.value, 'request_id': uuid.uuid4().hex}
        self.payloads.clear()
        self.timeouts.clear()
        self.raw_responses.clear()
        self.reads = self.leases = 0
        self.fake_mime = mime
        self.behavior = lambda _payload, _number: payload

    def assert_private_absent(self, generation):
        raw = json.dumps(generation, ensure_ascii=False)
        for secret in (self.nonce, self.private_wire, '기밀분야 지식 그래프', 'private-source'):
            self.assertNotIn(secret, raw)

    def failure(self, code='local_llm_tool_protocol_invalid', *, posts=1, reads=0,
                observations=1):
        with patch.object(chat, 'failure_generation', wraps=chat.failure_generation) as failure:
            result = self.run_input()
        self.assertEqual(failure.call_count, 1, result)
        self.assert_failed(result, code, posts=posts, reads=reads)
        saved = get_request(self.root, self.value['request_id'])
        self.assertEqual(saved['state'], 'failed')
        generation = saved['generation']
        self.assertEqual(generation['terminal_state'], 'failed')
        self.assertEqual(generation['error_code'], code)
        self.assert_private_absent(generation)
        before = (len(self.payloads), self.reads, self.leases, len(self.raw_responses))
        replay = self.run_input()
        self.assertFalse(replay['ok'], replay)
        self.assertEqual(replay['state'], 'failed')
        self.assertEqual(replay['error_code'], code)
        self.assertEqual(replay['generation'], generation)
        self.assertEqual((len(self.payloads), self.reads, self.leases, len(self.raw_responses)), before,
                         'replay performed transport or source read')
        # Assert the security/read/replay guards before the feature assertion:
        # HEAD must fail here because the observation is absent, not on safety.
        self.assertIn('wire_observations', generation['tool_loop'], 'persisted wire observations absent')
        rows = generation['tool_loop']['wire_observations']
        self.assertEqual(len(rows), observations, generation)
        for index, row in enumerate(rows):
            self.assertEqual(row['origin'], 'local_http_completion')
            self.assertEqual(row['response_bytes'], len(self.raw_responses[index]))
            self.assertIs(row['allow_tool'], index == 0)
            self.assertFalse(row.keys() - ALLOWED)
        return rows

    def test_duplicate_two_calls_persist_before_rejection_and_replay_without_read(self):
        payload = completion()
        message = payload['choices'][0]['message']
        message['content'] = self.private_wire
        message['tool_calls'] *= 2
        self.prepare(payload)
        rows = self.failure()
        self.assertEqual(rows[0]['tool_call_count'], 2)
        self.assertEqual(rows[0]['finish_reason'], 'tool_calls')

    def test_finish_missing_unknown_length_null_and_other_terminations(self):
        for finish, expected, code in (
            ('MISSING', 'missing', 'local_llm_incomplete_response'),
            (self.private_wire, 'unknown', 'local_llm_incomplete_response'),
            ('length', 'length', 'local_llm_reply_truncated'),
            (None, 'null', 'local_llm_incomplete_response'),
            (True, 'invalid_type', 'local_llm_incomplete_response'),
            ('content_filter', 'content_filter', 'local_llm_incomplete_response'),
            ('function_call', 'function_call', 'local_llm_incomplete_response')):
            with self.subTest(expected=expected):
                payload = completion()
                choice = payload['choices'][0]
                if finish == 'MISSING':
                    del choice['finish_reason']
                else:
                    choice['finish_reason'] = finish
                choice['message']['content'] = self.private_wire
                self.prepare(payload)
                self.assertEqual(self.failure(code)[0]['finish_reason'], expected)

    def test_invalid_outer_and_choices_shapes_record_without_execution(self):
        cases = [([], {'outer_state': 'invalid_type'}),
                 (None, {'outer_state': 'invalid_type'}),
                 ({'id': self.private_wire}, {'choices_state': 'missing'}),
                 ({**completion(), 'choices': {}}, {'choices_state': 'invalid_type'}),
                 ({**completion(), 'choices': []}, {'choice_count': 0}),
                 ({**completion(), 'choices': [None]}, {'first_choice_state': 'invalid_type'}),
                 ({**completion(), 'choices': completion()['choices'] * 2}, {'choice_count': 2})]
        for payload, expected in cases:
            with self.subTest(expected=expected):
                # JSON null needs bytes: the reused helper reserves Python None
                # as its default-response sentinel.
                self.prepare(b'null' if payload is None else payload)
                row = self.failure()[0]
                for key, value in expected.items():
                    self.assertEqual(row[key], value)

    def test_invalid_message_and_tool_calls_shapes_record_without_read(self):
        cases = [('MISSING', 'missing'), (None, 'invalid_type'),
                 ([], 'invalid_type'), (self.private_wire, 'invalid_type')]
        for message, state in cases:
            with self.subTest(message=state, value=type(message).__name__):
                payload = completion()
                if message == 'MISSING':
                    del payload['choices'][0]['message']
                else:
                    payload['choices'][0]['message'] = message
                self.prepare(payload)
                self.assertEqual(self.failure()[0]['message_state'], state)
        for calls, state in ((None, 'null'), ({}, 'invalid_type'),
                             (self.private_wire, 'invalid_type'), ([], 'list')):
            with self.subTest(calls=state, value=type(calls).__name__):
                payload = completion()
                payload['choices'][0]['message']['tool_calls'] = calls
                self.prepare(payload)
                self.assertEqual(self.failure()[0]['tool_calls_state'], state)
        payload = completion()
        del payload['choices'][0]['message']['tool_calls']
        self.prepare(payload)
        self.assertEqual(self.failure()[0]['tool_calls_state'], 'missing')

    def test_strict_index_zero_rejections_are_observed_before_source_read(self):
        for index in (False, True, 0.0, '0', -1, 1, None):
            with self.subTest(index=repr(index)):
                payload = completion()
                payload['choices'][0]['index'] = index
                self.prepare(payload)
                self.assertIs(self.failure()[0]['choice_index_valid'], False)
        payload = completion()
        del payload['choices'][0]['index']
        self.prepare(payload)
        self.assertIs(self.failure()[0]['choice_index_valid'], False)

    def test_legacy_call_and_nonempty_arguments_rejected_without_leak_or_read(self):
        for field in ('legacy', 'arguments'):
            with self.subTest(field=field):
                payload = completion()
                message = payload['choices'][0]['message']
                if field == 'legacy':
                    message['function_call'] = None
                else:
                    message['tool_calls'][0]['function']['arguments'] = json.dumps({'query': self.private_wire})
                self.prepare(payload)
                row = self.failure()[0]
                if field == 'legacy':
                    self.assertTrue(row['legacy_function_call_present'])

    def test_second_hop_tool_request_records_two_observations_and_only_one_read(self):
        second = completion(identifier='reply-2')
        second['choices'][0]['message']['content'] = self.private_wire
        self.behavior = lambda _payload, number: second if number == 2 else None
        rows = self.failure(posts=2, reads=1, observations=2)
        self.assertEqual([row['finish_reason'] for row in rows], ['tool_calls', 'tool_calls'])
        self.assertEqual([row['tool_call_count'] for row in rows], [1, 1])

    def test_valid_tool_then_final_persists_two_rows_without_source_content(self):
        result = self.run_input()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['reply'], self.nonce)
        self.assertEqual((len(self.payloads), self.reads, self.leases), (2, 1, 1))
        self.assertIn(self.nonce, self.payloads[1]['messages'][-1]['content'])
        saved = get_request(self.root, self.value['request_id'])
        self.assertEqual(saved['generation'], result['generation'])
        replay = self.run_input()
        self.assertEqual(replay['generation'], result['generation'])
        self.assertEqual((len(self.payloads), self.reads, self.leases), (2, 1, 1))
        generation = saved['generation']
        self.assert_private_absent(generation)
        self.assertIn('wire_observations', generation['tool_loop'], 'persisted wire observations absent')
        rows = generation['tool_loop']['wire_observations']
        self.assertEqual(len(rows), 2)
        self.assertEqual([row['finish_reason'] for row in rows], ['tool_calls', 'stop'])
        self.assertEqual([row['allow_tool'] for row in rows], [True, False])
        self.assertEqual([row['response_bytes'] for row in rows], list(map(len, self.raw_responses)))
        for forbidden in (self.nonce, MODEL, 'reply-1', 'reply-2', 'read-1', 'arguments', 'content'):
            self.assertNotIn(forbidden, json.dumps(rows))
        self.assertLessEqual(len(encoded(generation)), 4096)

    def test_no_call_final_persists_one_row_and_replay_has_no_http(self):
        self.prepare(completion('final', text='자료를 조회하지 않았습니다'))
        result = self.run_input()
        self.assertTrue(result['ok'], result)
        self.assertEqual((len(self.payloads), self.reads), (1, 0))
        saved = get_request(self.root, self.value['request_id'])
        self.assertEqual(saved['generation'], result['generation'])
        self.assertEqual(self.run_input()['generation'], saved['generation'])
        self.assertEqual((len(self.payloads), self.reads), (1, 0))
        generation = saved['generation']
        self.assertEqual(generation['tool_loop']['state'], 'no_call')
        self.assertIn('wire_observations', generation['tool_loop'], 'persisted wire observations absent')
        rows = generation['tool_loop']['wire_observations']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['finish_reason'], 'stop')
        self.assertEqual(rows[0]['tool_calls_state'], 'missing')

    def test_each_fresh_generation_owns_a_separate_observation_list(self):
        observed = []
        original = loop._post

        def capture(*args, **kwargs):
            rows = kwargs.get('observations')
            observed.append(rows)
            return original(*args, **kwargs)

        with patch.object(loop, '_post', side_effect=capture):
            self.prepare(completion('final'))
            first = self.run_input()
            self.assertTrue(first['ok'], first)
            self.prepare(completion('final'))
            second = self.run_input()
            self.assertTrue(second['ok'], second)
        self.assertEqual((len(observed), len(self.payloads), self.reads), (2, 1, 0))
        self.assertIsInstance(observed[0], list, 'generate did not pass host-owned list')
        self.assertIsInstance(observed[1], list)
        self.assertIsNot(observed[0], observed[1])
        self.assertEqual([len(rows) for rows in observed], [1, 1])

    def test_post_without_observations_preserves_direct_caller(self):
        self.prepare(completion('final'))
        adapter = SimpleNamespace(model=MODEL, reasoning_effort=None, max_tokens=64,
            base_url='http://127.0.0.1:11234/v1',
            last_metrics={'request': {'conversation_id': 'a' * 32, 'turn_id': 1,
                                     'context_version': 1, 'local_request_id': 'b' * 32}},
            _require_selected_model_ready=lambda _token: None)
        token = loop.KnowledgeDeadlineToken(self.token, loop.time.monotonic() + loop.TOTAL_SECONDS)
        result = loop._post(adapter, [{'role': 'user', 'content': 'fixture'}], token,
                            allow_tool=False, checkpoint=lambda: None, source_fence=lambda: None)
        self.assertEqual(result['kind'], 'final')
        self.assertEqual((len(self.payloads), self.reads), (1, 0))

    def test_mime_invalid_json_duplicate_keys_and_oversize_have_no_decoded_row(self):
        duplicate = b'{"id":"duplicate",' + json.dumps(completion()).encode()[1:]
        cases = ((completion(), 'text/event-stream', 'local_llm_tool_protocol_invalid'),
                 (b'{invalid PRIVATE', 'application/json', 'local_llm_tool_protocol_invalid'),
                 (duplicate, 'application/json', 'local_llm_tool_protocol_invalid'),
                 (b' ' * 65537, 'application/json', 'local_llm_response_too_large'),
                 (b'\xff', 'application/json', 'local_llm_tool_protocol_invalid'),
                 (b'{"choices": NaN}', 'application/json', 'local_llm_tool_protocol_invalid'))
        for payload, mime, code in cases:
            with self.subTest(mime=mime, code=code, body_type=type(payload).__name__):
                self.prepare(payload, mime=mime)
                self.assertEqual(self.failure(code, observations=0), [])


if __name__ == '__main__':
    unittest.main()
