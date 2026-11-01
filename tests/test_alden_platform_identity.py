"""Native ownership/failure boundaries and fresh account/device verification."""
import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/'scripts'),
               str(Path(__file__).resolve().parents[1]/'docs/evaluation/messenger-stream-20261021')]
import alden_platform_identity as native
import messenger_candidate as candidate
FRAMEWORKS = native._frameworks

FIRST = '12345678-1234-1234-1234-1234567890AB'
SECOND = '12345678-1234-1234-1234-1234567890CD'


class PlatformIdentityTests(unittest.TestCase):
    def setUp(self):
        self.io = SimpleNamespace(IOServiceMatching=Mock(return_value=11),
            IOServiceGetMatchingService=Mock(return_value=22),
            IORegistryEntryCreateCFProperty=Mock(return_value=44),IOObjectRelease=Mock())
        self.cf = SimpleNamespace(CFStringCreateWithCString=Mock(return_value=33),
            CFGetTypeID=Mock(return_value=7),CFStringGetTypeID=Mock(return_value=7),CFRelease=Mock())
        def write(_value, buffer, _length, _encoding):
            data = FIRST.encode()+b'\0';ctypes.memmove(buffer,data,len(data));return 1
        self.cf.CFStringGetCString=Mock(side_effect=write)
        self.enterContext(patch.object(native.sys,'platform','darwin'))
        self.enterContext(patch.object(native,'_frameworks',return_value=(self.io,self.cf)))

    def test_current_property_is_read_every_call_and_owned_objects_are_released(self):
        self.assertEqual(native.platform_uuid(), FIRST)
        def changed(_value, buffer, _length, _encoding):
            data=SECOND.encode()+b'\0';ctypes.memmove(buffer,data,len(data));return 1
        self.cf.CFStringGetCString.side_effect=changed
        self.assertEqual(native.platform_uuid(), SECOND)
        self.assertEqual(self.io.IORegistryEntryCreateCFProperty.call_count,2)
        self.assertEqual(self.cf.CFRelease.call_args_list, [((44,),),((33,),),((44,),),((33,),)])
        self.assertEqual(self.io.IOObjectRelease.call_count,2)

    def test_matching_and_service_failure_do_not_use_a_fallback(self):
        self.io.IOServiceMatching.return_value=0
        with self.assertRaisesRegex(native.PlatformIdentityError,'matching_unavailable'):native.platform_uuid()
        self.io.IOServiceGetMatchingService.assert_not_called()
        self.io.IOServiceMatching.return_value=11;self.io.IOServiceGetMatchingService.return_value=0
        with self.assertRaisesRegex(native.PlatformIdentityError,'service_unavailable'):native.platform_uuid()
        self.cf.CFRelease.assert_not_called();self.io.IOObjectRelease.assert_not_called()

    def test_key_failure_still_releases_service(self):
        self.cf.CFStringCreateWithCString.return_value=0
        with self.assertRaisesRegex(native.PlatformIdentityError,'key_unavailable'):native.platform_uuid()
        self.io.IOObjectRelease.assert_called_once_with(22);self.cf.CFRelease.assert_not_called()

    def test_missing_or_wrong_type_property_releases_all_acquired_objects(self):
        self.io.IORegistryEntryCreateCFProperty.return_value=0
        with self.assertRaisesRegex(native.PlatformIdentityError,'property_unavailable'):native.platform_uuid()
        self.cf.CFRelease.assert_called_once_with(33)
        self.cf.CFRelease.reset_mock();self.io.IORegistryEntryCreateCFProperty.return_value=44
        self.cf.CFGetTypeID.return_value=8
        with self.assertRaisesRegex(native.PlatformIdentityError,'type_invalid'):native.platform_uuid()
        self.cf.CFStringGetCString.assert_not_called()
        self.assertEqual(self.cf.CFRelease.call_count,2)

    def test_bounded_conversion_and_invalid_uuid_fail_closed(self):
        self.cf.CFStringGetCString.side_effect=None;self.cf.CFStringGetCString.return_value=0
        with self.assertRaisesRegex(native.PlatformIdentityError,'string_unavailable'):native.platform_uuid()
        self.cf.CFStringGetCString.return_value=1
        with self.assertRaisesRegex(native.PlatformIdentityError,'uuid_invalid'):native.platform_uuid()
        self.assertEqual(self.io.IOObjectRelease.call_count,2)

    def test_unsupported_platform_does_not_load_frameworks(self):
        with patch.object(native.sys,'platform','linux'),patch.object(native,'_frameworks') as load:
            with self.assertRaisesRegex(native.PlatformIdentityError,'not_macos'):native.platform_uuid()
            load.assert_not_called()

    def test_reader_resolves_current_account_and_device_every_time(self):
        resolve=Mock(side_effect=[('owner-a','verified'),('owner-b','verified')])
        reader=candidate.KakaoReader(SimpleNamespace(resolve_identity=resolve))
        with patch.object(candidate,'platform_uuid',side_effect=[FIRST,SECOND]):
            self.assertEqual([reader.identity(),reader.identity()], ['owner-a','owner-b'])
        self.assertEqual(resolve.call_args_list,[((FIRST,),),((SECOND,),)])

    def test_platform_failure_blocks_before_account_or_body_access(self):
        canonical=SimpleNamespace(resolve_identity=Mock(),snapshot=Mock())
        reader=candidate.KakaoReader(canonical)
        with patch.object(candidate,'platform_uuid',side_effect=native.PlatformIdentityError('unavailable')):
            with self.assertRaisesRegex(candidate.SourceBlocked,'current_identity_unavailable'):reader.page('a',None)
        canonical.resolve_identity.assert_not_called();canonical.snapshot.assert_not_called()

    def test_account_resolution_failure_is_not_replaced_by_cached_identity(self):
        reader=candidate.KakaoReader(SimpleNamespace(resolve_identity=Mock(side_effect=ValueError('current_account_unavailable'))))
        with patch.object(candidate,'platform_uuid',return_value=FIRST):
            with self.assertRaisesRegex(ValueError,'current_account_unavailable'):reader.identity()

    def test_framework_binding_uses_only_system_paths_and_pointer_safe_signatures(self):
        FRAMEWORKS.cache_clear();self.addCleanup(FRAMEWORKS.cache_clear)
        with patch.object(native.ctypes,'CDLL',side_effect=[self.io,self.cf]) as load:
            self.assertEqual(FRAMEWORKS(),(self.io,self.cf))
            self.assertEqual(FRAMEWORKS(),(self.io,self.cf))
        self.assertEqual(load.call_count,2)
        self.assertTrue(all(call.args[0].startswith('/System/Library/Frameworks/') for call in load.call_args_list))
        self.assertEqual(self.io.IOServiceGetMatchingService.restype,ctypes.c_uint32)
        self.assertEqual(self.io.IORegistryEntryCreateCFProperty.restype,ctypes.c_void_p)
        self.assertEqual(self.cf.CFStringGetCString.argtypes[2],ctypes.c_long)
        self.assertEqual(self.cf.CFRelease.restype,None)

    def test_framework_load_failure_is_explicit_without_cached_identity(self):
        FRAMEWORKS.cache_clear();self.addCleanup(FRAMEWORKS.cache_clear)
        with patch.object(native.ctypes,'CDLL',side_effect=OSError('not available')):
            with self.assertRaisesRegex(native.PlatformIdentityError,'framework_unavailable'):FRAMEWORKS()

    def test_non_ascii_uuid_is_rejected_and_native_objects_are_released(self):
        def invalid(_value,buffer,_length,_encoding):
            data='사용자정보'.encode()+b'\0';ctypes.memmove(buffer,data,len(data));return 1
        self.cf.CFStringGetCString.side_effect=invalid
        with self.assertRaisesRegex(native.PlatformIdentityError,'uuid_invalid'):native.platform_uuid()
        self.assertEqual(self.cf.CFRelease.call_count,2)
        self.io.IOObjectRelease.assert_called_once_with(22)


if __name__=='__main__':unittest.main()
