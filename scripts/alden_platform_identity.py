"""Fresh macOS platform UUID through system IOKit; never cache identity values."""
import ctypes
from functools import lru_cache
import re
import sys


class PlatformIdentityError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _frameworks():
    # Cache function handles only. UUID and account values are read every time.
    try:
        io = ctypes.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
        cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        signatures = [
            (io.IOServiceMatching, [ctypes.c_char_p], ctypes.c_void_p),
            (io.IOServiceGetMatchingService, [ctypes.c_uint32, ctypes.c_void_p], ctypes.c_uint32),
            (io.IORegistryEntryCreateCFProperty,
             [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32], ctypes.c_void_p),
            (io.IOObjectRelease, [ctypes.c_uint32], ctypes.c_int),
            (cf.CFStringCreateWithCString, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32], ctypes.c_void_p),
            (cf.CFGetTypeID, [ctypes.c_void_p], ctypes.c_ulong),
            (cf.CFStringGetTypeID, [], ctypes.c_ulong),
            (cf.CFStringGetCString,
             [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_uint32], ctypes.c_ubyte),
            (cf.CFRelease, [ctypes.c_void_p], None),
        ]
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result
        return io, cf
    except (OSError, AttributeError) as error:
        raise PlatformIdentityError('platform_framework_unavailable') from error


def platform_uuid():
    """Read the current property and release every owned native object."""
    if sys.platform != 'darwin':
        raise PlatformIdentityError('platform_not_macos')
    io, cf = _frameworks()
    matching = io.IOServiceMatching(b'IOPlatformExpertDevice')
    if not matching:
        raise PlatformIdentityError('platform_matching_unavailable')
    # IOServiceGetMatchingService consumes matching, including on failure.
    service = io.IOServiceGetMatchingService(0, matching)
    if not service:
        raise PlatformIdentityError('platform_service_unavailable')
    key, value = None, None
    try:
        utf8 = 0x08000100
        key = cf.CFStringCreateWithCString(None, b'IOPlatformUUID', utf8)
        if not key:
            raise PlatformIdentityError('platform_property_key_unavailable')
        value = io.IORegistryEntryCreateCFProperty(service, key, None, 0)
        if not value:
            raise PlatformIdentityError('platform_property_unavailable')
        if cf.CFGetTypeID(value) != cf.CFStringGetTypeID():
            raise PlatformIdentityError('platform_property_type_invalid')
        buffer = ctypes.create_string_buffer(64)
        if not cf.CFStringGetCString(value, buffer, len(buffer), utf8):
            raise PlatformIdentityError('platform_property_string_unavailable')
        try:
            result = buffer.value.decode('ascii')
        except UnicodeError as error:
            raise PlatformIdentityError('platform_uuid_invalid') from error
        if not re.fullmatch(r'[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}', result):
            raise PlatformIdentityError('platform_uuid_invalid')
        return result
    finally:
        if value:
            cf.CFRelease(value)
        if key:
            cf.CFRelease(key)
        io.IOObjectRelease(service)
