from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import digest
import alden_semantic_affinity as semantic
import alden_layout_affinity as layout


class AffinityJsonTests(unittest.TestCase):
    def test_duplicate_escaped_keys_and_overflow_are_rejected_in_retained_sources(self):
        with TemporaryDirectory() as folder:
            folder = Path(folder).resolve()
            for raw, reason in ((b'{"body":"first","body":"last"}', 'affinity_duplicate_json_key'),
                    (b'{"a":1,"\\u0061":2}', 'affinity_duplicate_json_key'),
                    (b'{"nested":[1e400]}', 'affinity_nonfinite_json'),
                    (b'{"nested":[NaN]}', 'affinity_nonfinite_json')):
                name = digest(raw) + '.json'; path = folder / name
                path.write_bytes(raw); before = path.read_bytes()
                guard = semantic._Guard(semantic.Budgets(), lambda: False)
                with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, '^' + reason + '$'):
                    semantic._source(folder, name, guard)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(guard.counts['source_bytes'], len(raw))
                self.assertEqual(semantic._json('{"ok":["NaN",1.5]}'), {'ok': ['NaN', 1.5]})

    def test_ambiguous_layout_query_is_rejected_before_any_database_open(self):
        with patch.object(layout.ReadTransactions, 'open', side_effect=AssertionError('unexpected open')):
            result = layout.read_action(Path('/not-opened'), '{"limit":1,"limit":2}')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'affinity_duplicate_json_key')

    def test_existing_encoding_and_nesting_error_contract_remain(self):
        self.assertEqual(semantic._json('{"값":1.5}'.encode('utf-16')), {'값': 1.5})
        with patch('alden_semantic_affinity._strict_json', side_effect=RecursionError):
            with self.assertRaisesRegex(ValueError, '^affinity_json_nesting$'):
                semantic._json('[]')
        with patch('alden_semantic_affinity._strict_json', side_effect=RuntimeError('affinity_cancelled')):
            with self.assertRaisesRegex(RuntimeError, '^affinity_cancelled$'):
                semantic._json('{}')


if __name__ == '__main__':
    unittest.main()
