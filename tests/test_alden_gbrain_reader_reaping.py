"""A reader's shutdown must not permit retirement while its child is alive."""
from pathlib import Path
import unittest
from unittest.mock import patch

import alden_gbrain_client as transport
from alden_gbrain_client import GbrainError,GbrainReadClient,GbrainWorkerUnreaped
from alden_gbrain_cache import _UnreapedWorker
from tests import test_alden_gbrain_import as fixtures


class ReaderReapingTests(unittest.TestCase):
    def reader(self,*,status=0,close_code=0,close_error=None,poll_error=None):
        class Peer:
            def poll(self):
                if poll_error is not None:raise poll_error
                return status
        class Wire:
            proc=Peer()
            def close(self):
                if close_error is not None:raise close_error
                return close_code
            def request(self,*args):return {'protocolVersion':'wrong'}
        client=object.__new__(GbrainReadClient)
        client.transport=Wire();client.check=lambda:None;client.timeout=1;client.source_id='default'
        client.root=Path('/owned/cache');client.home=Path('/owned/cache/generation/home');client.binary=Path('/owned/runtime/gbrain');client.lease_fd=None
        return client

    def test_legacy_cache_recovery_type_is_the_transport_type(self):
        self.assertIs(_UnreapedWorker,GbrainWorkerUnreaped)

    def test_clean_exit_discards_transport(self):
        reader=self.reader();reader.__exit__(None,None,None)
        self.assertIsNone(reader.transport)

    def test_closed_nonzero_exit_is_rejected(self):
        reader=self.reader(status=7,close_code=7)
        with self.assertRaisesRegex(GbrainError,'gbrain_unclean_shutdown'):
            reader.__exit__(None,None,None)
        self.assertIsNone(reader.transport)

    def test_alive_or_unknown_child_requires_recovery_before_body_errors(self):
        for body_error in (None,PermissionError('synthetic denial'),RuntimeError('synthetic cancellation')):
            for poll_error in (None,OSError('synthetic poll failure')):
                with self.subTest(body=type(body_error).__name__,poll=type(poll_error).__name__):
                    reader=self.reader(status=None,close_code=None,poll_error=poll_error)
                    wire=reader.transport
                    with self.assertRaisesRegex(_UnreapedWorker,'gbrain_cache_worker_not_reaped'):
                        reader.__exit__(type(body_error) if body_error else None,body_error,None)
                    self.assertIs(reader.transport,wire)

    def test_alive_child_does_not_hide_behind_close_error(self):
        reader=self.reader(status=None,close_error=OSError('synthetic close failure'))
        with self.assertRaises(_UnreapedWorker):reader.__exit__(None,None,None)

    def test_closed_child_preserves_original_body_failure(self):
        reader=self.reader(status=0,close_code=2)
        error=PermissionError('synthetic source denial')
        self.assertIsNone(reader.__exit__(PermissionError,error,None))
        self.assertIsNone(reader.transport)

    def test_failed_handshake_with_alive_child_keeps_recovery_type(self):
        reader=self.reader(status=None,close_code=None);wire=reader.transport
        reader.transport=None
        with patch.object(transport,'_Stdio',return_value=wire):
            with self.assertRaises(_UnreapedWorker):reader.__enter__()
        self.assertIs(reader.transport,wire)

    def test_failed_handshake_with_closed_child_keeps_peer_error(self):
        reader=self.reader();wire=reader.transport;reader.transport=None
        with patch.object(transport,'_Stdio',return_value=wire):
            with self.assertRaisesRegex(GbrainError,'gbrain_peer_mismatch'):reader.__enter__()
        self.assertIsNone(reader.transport)

    def test_cache_refresh_retains_owned_generation_for_unreaped_reader(self):
        fixture=fixtures.FullImportTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        reader=self.reader(status=None,close_code=None)
        def build(*args,**kwargs):
            try:
                raise PermissionError('synthetic denial')
            except PermissionError as error:
                reader.__exit__(PermissionError,error,error.__traceback__)
                raise
        with patch.object(fixture.cache,'_build',side_effect=build):
            with self.assertRaises(_UnreapedWorker):fixture.cache.refresh(incremental=True)
        self.assertIsNone(fixture.cache.active())
        self.assertEqual(len(list(fixture.cache.generations.iterdir())),1)


if __name__=='__main__':unittest.main()
