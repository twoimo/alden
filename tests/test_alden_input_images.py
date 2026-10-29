from __future__ import annotations
import base64
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import alden_input_images as images

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
GIF=base64.b64decode('R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==')
WEBP=base64.b64decode('UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA')

class ImageDecodeTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform=='darwin','Installed native macOS decoder required')
    def test_native_png_gif_and_webp_parse_pixels_and_preserve_original_bytes(self):
        for mime,raw in [('image/png',PNG),('image/gif',GIF),('image/webp',WEBP)]:
            before=raw
            with self.subTest(mime=mime):
                parsed=images.validate_image_bytes(raw,mime)
                self.assertEqual((parsed['width'],parsed['height'],parsed['frames']),(1,1,1))
                self.assertGreater(parsed['decoded_bytes'],0)
                self.assertEqual(raw,before)

    @unittest.skipUnless(sys.platform=='darwin','Installed native macOS decoder required')
    def test_native_gif_reads_every_frame_instead_of_silently_discarding_animation(self):
        animated=GIF[:-1]+GIF[19:-1]+b';'
        parsed=images.validate_image_bytes(animated,'image/gif')
        self.assertEqual(parsed['frames'],2);self.assertEqual(parsed['pixels'],2)
        self.assertEqual(parsed['decoded_bytes'],8)

    @unittest.skipUnless(sys.platform=='darwin','Installed native macOS decoder required')
    def test_native_jpeg_generated_from_owned_test_fixture(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'fixture.png';target=Path(folder)/'fixture.jpg'
            source.write_bytes(PNG)
            subprocess.run(['/usr/bin/sips','-s','format','jpeg',str(source),'--out',str(target)],
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5,check=True)
            self.assertEqual(images.validate_image_bytes(target.read_bytes(),'image/jpeg')['pixels'],1)

    @unittest.skipUnless(sys.platform=='darwin','Installed native macOS decoder required')
    def test_native_corrupt_truncated_text_and_mime_mismatch_fail(self):
        for raw,mime in [(b'not an image','image/png'),(PNG[:24],'image/png'),
                         (GIF[:10],'image/gif'),(WEBP[:14],'image/webp'),(PNG,'image/jpeg')]:
            with self.subTest(length=len(raw),mime=mime),self.assertRaisesRegex(ValueError,'^assistant_image_invalid$'):
                images.validate_image_bytes(raw,mime)

    @unittest.skipUnless(sys.platform=='darwin','Installed native macOS decoder required')
    def test_native_metadata_pixel_budget_precedes_raster_decode(self):
        def chunk(kind,data):
            return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
        compressor=zlib.compressobj()
        row=b'\0'+b'\0'*(5000*3)
        parts=[compressor.compress(row) for _ in range(5000)];parts.append(compressor.flush())
        oversized=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',5000,5000,8,2,0,0,0))
                   +chunk(b'IDAT',b''.join(parts))+chunk(b'IEND',b''))
        with self.assertRaisesRegex(ValueError,'^assistant_image_pixel_budget$'):
            images.validate_image_bytes(oversized,'image/png')

    def test_input_byte_budget_does_not_start_decoder(self):
        with patch.object(images,'MAX_BYTES',2),patch('alden_input_images.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError,'^assistant_image_budget$'):
                images.validate_image_bytes(PNG,'image/png')
            run.assert_not_called()

    def test_timeout_and_unavailable_decoder_remain_distinct(self):
        for failure,code in [(subprocess.TimeoutExpired('decoder',8),'assistant_image_decoder_timeout'),
                             (OSError('unavailable'),'assistant_image_decoder_unavailable')]:
            with self.subTest(code=code),patch('alden_input_images.subprocess.run',side_effect=failure):
                with self.assertRaisesRegex(ValueError,'^'+code+'$'):images.validate_image_bytes(PNG,'image/png')

    def test_malformed_and_unbounded_decoder_receipts_fail_without_raw_details(self):
        for raw in (b'[]',b'not JSON',b'x'*9000,
                    json.dumps({'ok':True,'parsed':{'mime':'image/png','frames':1,'pixels':images.MAX_PIXELS+1,'decoded_bytes':1}}).encode()):
            result=subprocess.CompletedProcess([],0,stdout=raw)
            with self.subTest(length=len(raw)),patch('alden_input_images.subprocess.run',return_value=result):
                with self.assertRaisesRegex(ValueError,'^assistant_image_invalid$'):images.validate_image_bytes(PNG,'image/png')

if __name__=='__main__':unittest.main()
