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
        for directory, name, source, _ in build.CANONICAL_TARGETS:
            binary = self.root / directory / name
            binary.parent.mkdir(parents=True, exist_ok=True)
            if not binary.exists(): binary.write_bytes(b'canonical executable')
            source_path = self.root / source
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text('fn main() {}')
            BuildDependencyTests.dep_info(binary.with_suffix('.d'), binary, [source_path])
        self.addCleanup(patch.stopall)
        patch.object(build, 'dependencies', side_effect=lambda root: {'npm': build.tree_digest(root/'desktop/node_modules', dependency=True)}).start()
        patch.object(build.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'pinned tool version', '')).start()
        self.receipt = {'input_digest': build.inputs(self.root)[0], 'cli_sha256': build.sha(self.cli),
                        'output_manifest': build.output_manifest(self.app),
                        'artifact_evidence': build.artifact_evidence(self.root)}

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

    def test_missing_dependency_evidence_never_invokes_toolchain_shims(self):
        self.npm.rename(self.npm.with_name('unavailable-dependencies'))
        with patch.object(build.subprocess,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'dependency_missing'):build.inputs(self.root)
            run.assert_not_called()

    def test_canonical_generated_bytes_are_checked_separately_from_source_key(self):
        directory, name, source, _ = build.CANONICAL_TARGETS[1]
        asset = self.root / 'desktop/dist/index.js'
        asset.parent.mkdir(); asset.write_text('asset 1')
        binary = self.root / directory / name
        BuildDependencyTests.dep_info(binary.with_suffix('.d'), binary, [self.root / source, asset])
        self.receipt['artifact_evidence'] = build.artifact_evidence(self.root)
        self.assertTrue(build.verified(self.root, self.receipt))
        asset.write_text('asset 2')
        self.assertEqual(build.inputs(self.root)[0], self.receipt['input_digest'])
        self.assertFalse(build.verified(self.root, self.receipt))

    def test_record_checks_source_stability_and_records_current_artifacts(self):
        key = self.receipt['input_digest']
        original = self.source.read_text()
        self.source.write_text(original + ' changed')
        argv = ['receipt', 'record', '--root', str(self.root), '--expected-key', key]
        with patch.object(sys, 'argv', argv):
            with self.assertRaisesRegex(RuntimeError, 'inputs_changed_during_build'):
                build.main()
        self.source.write_text(original)
        with patch.object(sys, 'argv', argv): build.main()
        receipt = json.loads((self.root / 'desktop/src-tauri/target/alden-build-receipt.json').read_text())
        self.assertEqual(receipt['artifact_evidence'], build.artifact_evidence(self.root))
        self.assertTrue(build.verified(self.root, receipt))


class BuildDependencyTests(unittest.TestCase):
    """Exercise real dep-info discovery without Cargo, downloads or user data."""

    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'current workspace'
        npm = self.root / 'desktop/node_modules'
        npm.mkdir(parents=True)
        (npm / 'dependency.js').write_text('dependency = 1')
        self.targets = [
            ('target/release', 'openkakao-cli', 'src/main.rs'),
            ('desktop/src-tauri/target/release', 'openkakao-alden-desktop',
             'desktop/src-tauri/src/main.rs'),
        ]
        for directory, name, source in self.targets:
            release = self.root / directory
            (release / 'deps').mkdir(parents=True)
            (release / name).write_bytes(b'current executable')
            source_path = self.write(self.root / source, 'fn main() {}')
            self.dep_info(release / (name + '.d'), release / name, [source_path])
        self.crate = self.base / 'registry/demo-1.0'
        self.write(self.crate / 'Cargo.toml', '[package]\nname = "demo"\nversion = "1.0"')
        self.library_source = self.write(self.crate / 'src/lib.rs', 'pub fn demo() {}')
        self.library_info = self.root / 'target/release/deps/demo-0123456789abcdef.d'
        self.dep_info(self.library_info, self.library_info, [self.library_source])
        self.other_library_info = self.root / 'desktop/src-tauri/target/release/deps/demo-0123456789abcdef.d'
        self.dep_info(self.other_library_info, self.other_library_info, [self.library_source])

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    @staticmethod
    def dep_info(path, target, sources):
        def escape(value):
            return str(value).replace('\\', '\\\\').replace(' ', '\\ ').replace('#', '\\#').replace('$', '$$')
        path.write_text(escape(target) + ': ' + ' '.join(map(escape, sources)) + '\n')

    def test_stale_root_binary_records_do_not_select_foreign_assets(self):
        stale = self.root / 'desktop/src-tauri/target/release/deps/openkakao_alden_desktop-0123456789abcdef.d'
        missing = self.base / 'previous/desktop/dist/assets/removed.js'
        self.dep_info(stale, stale, [missing])
        before = stale.read_bytes()
        result = build.dependencies(self.root)
        self.assertIn('npm', result)
        self.assertEqual(stale.read_bytes(), before)

    def test_canonical_target_must_belong_to_current_workspace(self):
        directory, name, source = self.targets[1]
        self.dep_info(self.root / directory / (name + '.d'), self.base / 'previous' / name,
                      [self.root / source])
        with self.assertRaisesRegex(RuntimeError, 'dependency.*target'):
            build.dependencies(self.root)

    def test_current_root_source_anchor_is_required(self):
        directory, name, _ = self.targets[1]
        foreign = self.write(self.base / 'previous/src/main.rs', 'fn main() {}')
        self.dep_info(self.root / directory / (name + '.d'), self.root / directory / name, [foreign])
        with self.assertRaisesRegex(RuntimeError, 'dependency.*source'):
            build.dependencies(self.root)

    def test_missing_current_asset_still_fails(self):
        directory, name, source = self.targets[1]
        missing = self.root / 'desktop/dist/missing.js'
        self.dep_info(self.root / directory / (name + '.d'), self.root / directory / name,
                      [self.root / source, missing])
        with self.assertRaisesRegex(RuntimeError, 'dependency_missing'):
            build.dependencies(self.root)

    def test_absent_tauri_watch_directory_is_recorded_and_creation_invalidates(self):
        directory, name, source = self.targets[1]
        watched = self.root / 'desktop/src-tauri/capabilities'
        self.dep_info(self.root / directory / (name + '.d'), self.root / directory / name,
                      [self.root / source, watched])
        before = build.dependencies(self.root)
        artifacts = build.artifact_evidence(self.root)
        self.assertEqual(before['watch:desktop/src-tauri/capabilities'], {'state': 'absent'})
        watched.mkdir()
        self.assertNotEqual(build.dependencies(self.root), before)
        self.assertNotEqual(build.artifact_evidence(self.root), artifacts)
        empty = build.dependencies(self.root)
        self.write(watched / 'default.json', '{"identifier":"example","permissions":[]}')
        self.assertNotEqual(build.dependencies(self.root), empty)

    def test_tauri_watch_directory_rejects_file_and_symlink(self):
        watched = self.root / 'desktop/src-tauri/capabilities'
        watched.write_text('not a directory')
        with self.assertRaisesRegex(RuntimeError, 'watch.*directory'):
            build.dependencies(self.root)
        watched.unlink()
        watched.symlink_to(self.base / 'missing-directory', target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            build.dependencies(self.root)

    def test_unknown_missing_watch_path_is_not_tolerated(self):
        directory, name, source = self.targets[1]
        missing = self.root / 'desktop/src-tauri/another-watch-directory'
        self.dep_info(self.root / directory / (name + '.d'), self.root / directory / name,
                      [self.root / source, missing])
        with self.assertRaisesRegex(RuntimeError, 'dependency_missing'):
            build.dependencies(self.root)

    def test_missing_external_library_still_fails_with_path(self):
        self.library_source.unlink()
        with self.assertRaisesRegex(RuntimeError, 'dependency_missing') as error:
            build.dependencies(self.root)
        self.assertIn(str(self.library_source), str(error.exception))

    def test_foreign_generated_file_is_hashed_without_enclosing_repository(self):
        foreign = self.base / 'previous'
        self.write(foreign / 'Cargo.toml', '[package]\nname = "previous"')
        generated = self.write(foreign / 'target/release/build/demo-0123456789abcdef/out/private.rs', 'generated 1')
        self.dep_info(self.library_info, self.library_info, [self.library_source, generated])
        before = build.dependencies(self.root)
        self.write(foreign / 'unrelated-source.txt', 'not a dependency')
        self.assertEqual(build.dependencies(self.root), before)
        generated.write_text('generated 2')
        self.assertNotEqual(build.dependencies(self.root), before)
        generated.unlink()
        with self.assertRaisesRegex(RuntimeError, 'dependency_missing'):
            build.dependencies(self.root)

    def test_external_crate_patch_invalidates_dependencies(self):
        before = build.dependencies(self.root)
        self.library_source.write_text('pub fn demo() { changed(); }')
        self.assertNotEqual(build.dependencies(self.root), before)

    def test_workspace_library_outside_standard_source_roots_is_hashed(self):
        local = self.write(self.root / 'packages/helper/src/lib.rs', 'local library 1')
        self.dep_info(self.library_info, self.library_info, [self.library_source, local])
        before = build.dependencies(self.root)
        local.write_text('local library 2')
        self.assertNotEqual(build.dependencies(self.root), before)

    def test_make_line_continuation_separates_dependencies(self):
        other = self.write(self.crate / 'src/other.rs', 'other')
        self.dep_info(self.library_info, self.library_info, [self.library_source, other])
        original = self.library_info.read_text()
        self.library_info.write_text(original.replace(str(self.library_source) + ' ',
                                                    str(self.library_source) + '\\\n'))
        self.assertIn('npm', build.dependencies(self.root))

    def test_dep_info_and_dependency_symlinks_are_rejected(self):
        before = self.library_info.read_text()
        backup = self.write(self.base / 'saved.d', before)
        self.library_info.unlink()
        self.library_info.symlink_to(backup)
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            build.dependencies(self.root)
        self.library_info.unlink(); self.library_info.write_text(before)
        self.library_source.unlink()
        self.library_source.symlink_to(self.write(self.base / 'replacement.rs', 'replacement'))
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            build.dependencies(self.root)

    def test_make_escaped_space_hash_and_dollar_are_literal_filename_bytes(self):
        special = self.write(self.crate / 'src/space # dollar$ and quotes\'".rs', 'special')
        self.dep_info(self.library_info, self.library_info, [self.library_source, special])
        before = build.dependencies(self.root)
        special.write_text('special changed')
        self.assertNotEqual(build.dependencies(self.root), before)

    def test_generated_file_through_symlink_directory_is_rejected(self):
        real = self.base / 'generated'
        self.write(real / 'private.rs', 'generated')
        alias = self.base / 'alias'
        alias.symlink_to(real, target_is_directory=True)
        self.dep_info(self.library_info, self.library_info, [self.library_source, alias / 'private.rs'])
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            build.dependencies(self.root)


if __name__ == '__main__': unittest.main()
