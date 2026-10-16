"""The product action stays ahead of enrollment/catalog mutation dispatch."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from tests.test_auto_reply_menubar import load

class QualityMenuTests(unittest.TestCase):
    def test_quality_reader_never_enters_config_mutation_or_creates_missing_store(self):
        with TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            module=load('alden_quality_menu_test')
            values={'--action':'collection-quality','--state-root':str(root),'--history-query':'{"scope":"all"}'}
            with patch.object(module,'_argv_flag_value',side_effect=lambda name:values.get(name)), \
                 patch.object(module,'_scope_menubar_rooms_to_enrollment',side_effect=AssertionError('mutation')), \
                 patch.object(module,'_apply_catalog_mutates',side_effect=AssertionError('mutation')), \
                 patch.object(module,'_print_json') as output:
                self.assertEqual(module.main(),0)
            self.assertEqual(output.call_args.args[0]['reason'],'quality_scope_unavailable')
            self.assertFalse((root/'knowledge').exists())
