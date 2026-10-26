"""A pending record must not survive source withdrawal at its commit boundary."""
import unittest
from unittest.mock import patch

import alden_gbrain_import as full
from alden_collection_export import ExportError
from tests.test_alden_gbrain_catchup_adversarial import _CatchupFixture,REJECTIONS


class CheckpointCommitTests(_CatchupFixture):
    def reject_late_withdrawal(self,*,mode,loss):
        self.native_source()
        generation,_=self.closed_candidate()
        original=full._write;committed=[]
        def write(path,value):
            result=original(path,value)
            if path==self.cache.root/'pending.json':
                committed.append(True)
                if loss=='withheld':self.authority['access']=False
                else:self.authority['stream']='c'*64
            return result
        if mode=='refresh':self.on_readback=lambda path:self.change_content()
        with patch.object(full,'_write',side_effect=write):
            with self.assertRaises(REJECTIONS):
                if mode=='stage':self.stage(generation)
                else:self.cache.refresh(incremental=True,catch_up=True,store_factory=self.factory)
        self.assertTrue(committed,'exercise the persisted pending boundary')
        self.assert_no_grant()

    def test_stage_commit_rechecks_per_record_withholding(self):
        self.reject_late_withdrawal(mode='stage',loss='withheld')

    def test_stage_commit_rechecks_access_stream(self):
        self.reject_late_withdrawal(mode='stage',loss='stream')

    def test_refresh_pending_commit_rechecks_per_record_withholding(self):
        self.reject_late_withdrawal(mode='refresh',loss='withheld')

    def test_refresh_pending_commit_rechecks_access_stream(self):
        self.reject_late_withdrawal(mode='refresh',loss='stream')

    def test_failed_cleanup_keeps_source_rejection_as_an_explicit_recovery_cause(self):
        self.native_source();generation,_=self.closed_candidate()
        original=full._write
        original_retire=self.cache._retire
        def write(path,value):
            result=original(path,value)
            if path==self.cache.root/'pending.json':self.authority['access']=False
            return result
        def retire(active,**options):
            if options.get('preserve') is None:raise OSError('synthetic cleanup failure')
            return original_retire(active,**options)
        with patch.object(full,'_write',side_effect=write), \
             patch.object(self.cache,'_retire',side_effect=retire):
            with self.assertRaisesRegex(full.GbrainError,'gbrain_cache_cleanup_required') as error:
                self.stage(generation)
        self.assertIsInstance(error.exception.__cause__,ExportError)
        self.assertEqual(str(error.exception.__cause__),'collection_export_source_withheld')
        self.assertEqual(error.exception.__notes__,['cleanup_error=OSError'])
        # A failed cleanup is a hold; the remaining pointer is not a reader grant.
        self.assertTrue((self.cache.root/'pending.json').exists())
        with self.assertRaisesRegex(full.GbrainError,'gbrain_cache_unavailable'):
            self.f.query(store_factory=self.factory)


if __name__=='__main__':unittest.main()
