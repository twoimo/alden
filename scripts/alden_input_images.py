"""Bounded native image parsing; original bytes are never changed.

Uses installed macOS ImageIO/CoreGraphics in a disposable subprocess. The
pixel/raster budgets bound decoded image data, not total process RSS.
API contracts: developer.apple.com/documentation/imageio/cgimagesource and
the installed Xcode ImageIO/CoreFoundation/CoreGraphics headers.
"""
from __future__ import annotations

import ctypes as C
import json
from pathlib import Path
import subprocess
import sys

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024
MAX_RASTER_BYTES = 128 * 1024 * 1024
MAX_FRAMES = 64
DECODE_TIMEOUT = 8
MIME_TYPES = {'public.png':'image/png', 'public.jpeg':'image/jpeg',
              'com.compuserve.gif':'image/gif', 'org.webmproject.webp':'image/webp'}


def _function(lib, name, args, result):
    function = getattr(lib, name)
    function.argtypes, function.restype = args, result
    return function


def _native_decode(data, mime):
    if sys.platform != 'darwin':
        raise ValueError('assistant_image_decoder_unavailable')
    try:
        cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        io = C.CDLL('/System/Library/Frameworks/ImageIO.framework/ImageIO')
        cg = C.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        pointer, size, long = C.c_void_p, C.c_size_t, C.c_long
        make_data = _function(cf,'CFDataCreate',[pointer,pointer,long],pointer)
        release = _function(cf,'CFRelease',[pointer],None)
        get_length = _function(cf,'CFDataGetLength',[pointer],long)
        to_string = _function(cf,'CFStringGetCString',[pointer,pointer,long,C.c_uint32],C.c_bool)
        dict_get = _function(cf,'CFDictionaryGetValue',[pointer,pointer],pointer)
        type_id = _function(cf,'CFGetTypeID',[pointer],C.c_ulong)
        number_id = _function(cf,'CFNumberGetTypeID',[],C.c_ulong)
        number_get = _function(cf,'CFNumberGetValue',[pointer,C.c_int,pointer],C.c_bool)
        make_source = _function(io,'CGImageSourceCreateWithData',[pointer,pointer],pointer)
        get_type = _function(io,'CGImageSourceGetType',[pointer],pointer)
        get_count = _function(io,'CGImageSourceGetCount',[pointer],size)
        get_status = _function(io,'CGImageSourceGetStatus',[pointer],C.c_int)
        frame_status = _function(io,'CGImageSourceGetStatusAtIndex',[pointer,size],C.c_int)
        properties = _function(io,'CGImageSourceCopyPropertiesAtIndex',[pointer,size,pointer],pointer)
        make_image = _function(io,'CGImageSourceCreateImageAtIndex',[pointer,size,pointer],pointer)
        image_width = _function(cg,'CGImageGetWidth',[pointer],size)
        image_height = _function(cg,'CGImageGetHeight',[pointer],size)
        row_bytes = _function(cg,'CGImageGetBytesPerRow',[pointer],size)
        bits_per_pixel = _function(cg,'CGImageGetBitsPerPixel',[pointer],size)
        provider = _function(cg,'CGImageGetDataProvider',[pointer],pointer)
        copy_pixels = _function(cg,'CGDataProviderCopyData',[pointer],pointer)
        width_key = C.c_void_p.in_dll(io,'kCGImagePropertyPixelWidth').value
        height_key = C.c_void_p.in_dll(io,'kCGImagePropertyPixelHeight').value
    except (OSError, AttributeError, ValueError) as error:
        raise ValueError('assistant_image_decoder_unavailable') from error

    native_data, source = None, None
    try:
        buffer = C.create_string_buffer(data)
        native_data = make_data(None,buffer,len(data))
        if not native_data:
            raise ValueError('assistant_image_invalid')
        source = make_source(native_data,None)
        if not source or get_status(source) != 0:
            raise ValueError('assistant_image_invalid')
        kind = C.create_string_buffer(256)
        uti = get_type(source)
        if not uti or not to_string(uti,kind,256,0x08000100):
            raise ValueError('assistant_image_invalid')
        actual_mime = MIME_TYPES.get(kind.value.decode('ascii'))
        if actual_mime != mime:
            raise ValueError('assistant_image_invalid')
        count = get_count(source)
        if not 1 <= count <= MAX_FRAMES:
            raise ValueError('assistant_image_pixel_budget')
        dimensions, pixels = [], 0
        for index in range(count):
            props = properties(source,index,None)
            if not props:
                raise ValueError('assistant_image_invalid')
            try:
                values = []
                for key in (width_key,height_key):
                    number = dict_get(props,key)
                    value = C.c_int64()
                    if not number or type_id(number) != number_id() or not number_get(number,4,C.byref(value)):
                        raise ValueError('assistant_image_invalid')
                    values.append(value.value)
                width,height = values
                if width <= 0 or height <= 0:
                    raise ValueError('assistant_image_invalid')
                pixels += width * height
                if pixels > MAX_PIXELS:
                    raise ValueError('assistant_image_pixel_budget')
                dimensions.append((width,height))
            finally:
                release(props)

        total_raster_bytes = 0
        for index,(width,height) in enumerate(dimensions):
            image = make_image(source,index,None)
            if not image:
                raise ValueError('assistant_image_invalid')
            raster = None
            try:
                if (image_width(image),image_height(image)) != (width,height):
                    raise ValueError('assistant_image_invalid')
                expected = row_bytes(image) * height
                if bits_per_pixel(image) > 64 or expected <= 0 or total_raster_bytes + expected > MAX_RASTER_BYTES:
                    raise ValueError('assistant_image_pixel_budget')
                data_provider = provider(image)
                raster = copy_pixels(data_provider) if data_provider else None
                if not raster or get_length(raster) < expected or frame_status(source,index) != 0:
                    raise ValueError('assistant_image_invalid')
                total_raster_bytes += get_length(raster)
                if total_raster_bytes > MAX_RASTER_BYTES:
                    raise ValueError('assistant_image_pixel_budget')
            finally:
                if raster: release(raster)
                release(image)
        return {'mime':actual_mime,'frames':count,'pixels':pixels,
                'decoded_bytes':total_raster_bytes,
                'width':dimensions[0][0],'height':dimensions[0][1]}
    finally:
        if source: release(source)
        if native_data: release(native_data)


def validate_image_bytes(data, mime):
    if not isinstance(data,bytes) or mime not in MIME_TYPES.values():
        raise ValueError('assistant_image_invalid')
    if not 0 < len(data) <= MAX_BYTES:
        raise ValueError('assistant_image_budget')
    try:
        result = subprocess.run([sys.executable,'-E','-B','-s',str(Path(__file__).resolve()),
                                 '--decode',mime],input=data,stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL,timeout=DECODE_TIMEOUT,check=False)
    except subprocess.TimeoutExpired as error:
        raise ValueError('assistant_image_decoder_timeout') from error
    except OSError as error:
        raise ValueError('assistant_image_decoder_unavailable') from error
    if len(result.stdout) > 8192:
        raise ValueError('assistant_image_invalid')
    try:
        receipt = json.loads(result.stdout)
    except (ValueError, UnicodeError) as error:
        raise ValueError('assistant_image_invalid') from error
    if not isinstance(receipt,dict):
        raise ValueError('assistant_image_invalid')
    if result.returncode != 0 or receipt.get('ok') is not True:
        code = receipt.get('error_code')
        if code not in {'assistant_image_invalid','assistant_image_budget',
                        'assistant_image_pixel_budget','assistant_image_decoder_unavailable'}:
            code = 'assistant_image_invalid'
        raise ValueError(code)
    parsed = receipt.get('parsed')
    if (not isinstance(parsed,dict) or parsed.get('mime') != mime
            or type(parsed.get('frames')) is not int or not 1 <= parsed['frames'] <= MAX_FRAMES
            or type(parsed.get('pixels')) is not int or not 0 < parsed['pixels'] <= MAX_PIXELS
            or type(parsed.get('decoded_bytes')) is not int or not 0 < parsed['decoded_bytes'] <= MAX_RASTER_BYTES):
        raise ValueError('assistant_image_invalid')
    return parsed


def main():
    if len(sys.argv) != 3 or sys.argv[1] != '--decode' or sys.argv[2] not in MIME_TYPES.values():
        return 2
    data = sys.stdin.buffer.read(MAX_BYTES+1)
    try:
        if not 0 < len(data) <= MAX_BYTES:
            raise ValueError('assistant_image_budget')
        result = {'ok':True,'parsed':_native_decode(data,sys.argv[2])}
        code = 0
    except ValueError as error:
        result = {'ok':False,'error_code':str(error)};code = 1
    print(json.dumps(result,allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
