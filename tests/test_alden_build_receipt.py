"""Prevent stale artifact reuse after dependency, resource or output changes."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import alden_build_receipt as build


class BuildReceiptTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.app = self.root/'desktop/src-tauri/target/release/bundle/macos/Alden.app'
        self.binary = self.app/'Contents/MacOS/Alden'
        self.binary.parent.mkdir(parents=True); self.binary.write_bytes(b'validated binary')
        self.cli = self.root/'target/release/openkakao-cli'
        self.cli.parent.mkdir(parents=True); self.cli.write_bytes(b'validated cli'); self.cli.chmod(0o755)
        self.source = self.root/'scripts/runtime.py'
        self.source.parent.mkdir(); self.source.write_text('runtime = 1')
        self.npm = self.root/'desktop/node_modules'
        self.npm.mkdir(); (self.npm/'dependency.js').write_text('dependency = 1')
        self.resource = self.root/'desktop/src-tauri/Info.plist'
        self.resource.write_text('resource version 1')
        self.addCleanup(patch.stopall)
        patch.object(build, 'dependencies', side_effect=lambda root: {'npm': build.tree_digest(root/'desktop/node_modules', dependency=True)}).start()
        patch.object(build.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'pinned tool version', '')).start()
        self.receipt = {'input_digest': build.inputs(self.root)[0], 'cli_sha256': build.sha(self.cli),
                        'output_manifest': build.output_manifest(self.app)}

    def test_unchanged_bundle_reused_without_rewriting_provenance(self):
        before = self.binary.stat().st_mtime_ns
        self.assertTrue(build.verified(self.root, self.receipt))
        self.assertEqual(self.binary.stat().st_mtime_ns, before)

    def test_installed_dependency_patch_invalidates_unchanged_lock(self):
        (self.npm/'dependency.js').write_text('dependency = 2')
        self.assertFalse(build.verified(self.root, self.receipt))

    def test_info_plist_source_and_signing_environment_invalidate(self):
        for path in [self.resource, self.source]:
            old = path.read_text(); path.write_text(old+' changed')
            self.assertFalse(build.verified(self.root, self.receipt)); path.write_text(old)
        with patch.dict(os.environ, {'OPENKAKAO_SIGN_IDENTITY': 'different identity'}):
            self.assertFalse(build.verified(self.root, self.receipt))

    def test_mutated_cli_bundle_or_signature_is_not_reused(self):
        self.cli.write_bytes(b'changed'); self.assertFalse(build.verified(self.root, self.receipt))
        self.cli.write_bytes(b'validated cli')
        self.binary.write_bytes(b'changed'); self.assertFalse(build.verified(self.root, self.receipt))
        self.binary.write_bytes(b'validated binary')
        with patch.object(build.subprocess, 'run', side_effect=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1 if cmd[0]=='codesign' else 0, 'pinned tool version', '')):
            self.assertFalse(build.verified(self.root, self.receipt))

    def test_executable_permission_loss_or_bundle_symlink_is_rejected(self):
        old = self.binary.stat().st_mode; self.binary.chmod(0o600)
        self.assertFalse(build.verified(self.root, self.receipt)); self.binary.chmod(old)
        (self.app/'injected').symlink_to(self.source)
        with self.assertRaisesRegex(RuntimeError, 'output_symlink'):
            build.verified(self.root, self.receipt)

    def test_external_npm_symlink_is_rejected_and_cache_is_not_input(self):
        before = build.tree_digest(self.npm, dependency=True)
        (self.npm/'.cache').mkdir(); (self.npm/'.cache/generated').write_text('generated')
        self.assertEqual(build.tree_digest(self.npm, dependency=True), before)
        (self.npm/'external').symlink_to(self.source)
        with self.assertRaisesRegex(RuntimeError, 'input_symlink'):
            build.tree_digest(self.npm, dependency=True)

    def test_concurrent_builder_does_not_enter_compilation(self):
        with (self.root/'target/alden-canonical-build.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(sys, 'argv', ['receipt', 'run', '--root', str(self.root)]), patch.object(build.subprocess, 'run') as run:
                with self.assertRaisesRegex(SystemExit, 'already running'): build.main()
                run.assert_not_called()


if __name__ == '__main__': unittest.main()
