import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import verify_model_provenance as verifier


class ModelProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def manifest(self, data=b'original', algorithm='sha256'):
        file = self.root / 'model.safetensors'
        file.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest() if algorithm == 'sha256' else hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
        manifest = {'schema_version': 1, 'repository': 'publisher/model', 'revision': 'a' * 40,
                    'files': [{'path': file.name, 'bytes': len(data), 'algorithm': algorithm, 'digest': digest}]}
        path = self.root / 'manifest.json'
        path.write_text(json.dumps(manifest))
        return path

    def test_real_hash_algorithms_and_readonly_file_metadata(self):
        for algorithm in ['sha256', 'git_blob_sha1']:
            manifest = verifier.load_manifest(self.manifest(algorithm=algorithm))
            file = self.root / 'model.safetensors'
            before = (file.stat().st_ino, file.stat().st_mtime_ns, file.read_bytes())
            result = verifier.verify_model(self.root, manifest)
            self.assertTrue(result['ok'])
            self.assertEqual(result['matched_files'], 1)
            self.assertEqual(result['network_requests'], 0)
            self.assertEqual(result['model_loads'], 0)
            self.assertFalse(result['runtime_inference_or_residency_verified'])
            self.assertEqual(before, (file.stat().st_ino, file.stat().st_mtime_ns, file.read_bytes()))

    def test_missing_wrong_size_and_same_size_corruption_fail(self):
        manifest = verifier.load_manifest(self.manifest())
        file = self.root / 'model.safetensors'
        for data, reason in [(b'bad', 'file_size_mismatch'), (b'corrupt!', 'file_hash_mismatch')]:
            file.write_bytes(data)
            result = verifier.verify_model(self.root, manifest)
            self.assertFalse(result['ok'])
            self.assertEqual(result['rows'][0]['reason'], reason)
        file.unlink()
        self.assertEqual(verifier.verify_model(self.root, manifest)['rows'][0]['reason'], 'file_unavailable')

    def test_leaf_symlinks_and_fifos_do_not_get_read(self):
        manifest = verifier.load_manifest(self.manifest())
        file = self.root / 'model.safetensors'
        file.rename(self.root / 'other')
        file.symlink_to(self.root / 'other')
        self.assertFalse(verifier.verify_model(self.root, manifest)['ok'])
        file.unlink(); os.mkfifo(file)
        self.assertEqual(verifier.verify_model(self.root, manifest)['rows'][0]['reason'], 'file_not_regular')

    def test_replacement_after_open_is_not_verified_as_the_current_path(self):
        manifest = verifier.load_manifest(self.manifest())
        file = self.root / 'model.safetensors'
        original_read = verifier.os.read
        replaced = False
        def replace_then_read(descriptor, count):
            nonlocal replaced
            if not replaced:
                replacement = self.root / 'replacement'
                replacement.write_bytes(b'corrupt!')
                replacement.replace(file)
                replaced = True
            return original_read(descriptor, count)
        with patch.object(verifier.os, 'read', side_effect=replace_then_read):
            result = verifier.verify_model(self.root, manifest)
        self.assertFalse(result['ok'])
        self.assertEqual(result['rows'][0]['reason'], 'file_changed_during_check')

    def test_manifest_path_escape_duplicate_unsupported_hash_and_bounds(self):
        path = self.manifest()
        original = json.loads(path.read_text())
        for key, value in [('path', '../model.safetensors'), ('path', '/absolute'), ('path', 'a/b'), ('bytes', True), ('bytes', verifier.MAX_FILE_BYTES + 1), ('algorithm', 'md5'), ('digest', 'a')]:
            item = json.loads(json.dumps(original)); item['files'][0][key] = value
            path.write_text(json.dumps(item))
            with self.assertRaises(ValueError): verifier.load_manifest(path)
        original['files'].append(original['files'][0].copy()); path.write_text(json.dumps(original))
        with self.assertRaises(ValueError): verifier.load_manifest(path)

    def test_too_large_manifest_and_invalid_revision_fail(self):
        path = self.manifest()
        item = json.loads(path.read_text()); item['revision'] = 'main'; path.write_text(json.dumps(item))
        with self.assertRaises(ValueError): verifier.load_manifest(path)
        path.write_bytes(b' ' * (verifier.MAX_MANIFEST_BYTES + 1))
        with self.assertRaisesRegex(ValueError, 'manifest_too_large'): verifier.load_manifest(path)


if __name__ == '__main__':
    unittest.main()
