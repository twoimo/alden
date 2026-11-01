"""CLI discovery contracts using synthetic paths; no app/account/DB access."""
import ast
import json
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
RESOLVERS = (
    "auto-reply-worker.py", "auto-reply-supervisor.py", "auto-reply-db-watch.py",
)


class CliNamingTests(unittest.TestCase):
    def resolver(self, script, files=(), environ=None, stable=None):
        class FixturePath(PurePosixPath):
            def resolve(self):
                return self

            def is_file(self):
                return str(self) in files

        tree = ast.parse((ROOT / "scripts" / script).read_text())
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "_find_cli_bin")
        namespace = {
            "Path": FixturePath, "os": SimpleNamespace(environ=environ or {}),
            "__file__": "/fixture/scripts/worker.py",
            "_stable_cli_path": lambda: FixturePath(stable) if stable else None,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), script, "exec"), namespace)
        return str(namespace["_find_cli_bin"]())

    def test_canonical_name_precedes_legacy_in_each_existing_location(self):
        directories = (
            "/fixture/bin", "/Applications/Alden.app/Contents/Resources/bin",
            "/Applications/AutoReplyMenu.app/Contents/Resources/bin", "/fixture/target/release",
        )
        for script in RESOLVERS:
            for directory in directories:
                canonical, legacy = (f"{directory}/{name}" for name in ("alden-cli", "openkakao-cli"))
                with self.subTest(script=script, directory=directory):
                    self.assertEqual(self.resolver(script, {canonical, legacy}), canonical)
                    self.assertEqual(self.resolver(script, {legacy}), legacy)

    def test_explicit_and_tcc_approved_paths_retain_precedence(self):
        for script in RESOLVERS:
            with self.subTest(script=script):
                self.assertEqual(self.resolver(script, {"/explicit"},
                                 {"OPENKAKAO_BINARY": "/explicit"}, "/approved/openkakao-cli"), "/explicit")
                self.assertEqual(self.resolver(script, {"/fixture/bin/alden-cli"},
                                 stable="/approved/openkakao-cli"), "/approved/openkakao-cli")
                self.assertEqual(self.resolver(script), "/fixture/target/release/alden-cli")

    def test_cargo_package_binary_and_compatibility_share_the_implementation(self):
        config = tomllib.loads((ROOT / "Cargo.toml").read_text())
        self.assertEqual(config["package"]["name"], "alden-cli")
        self.assertEqual(config["lib"]["name"], "openkakao_cli")
        bins = {entry["name"]: entry["path"] for entry in config["bin"]}
        self.assertEqual(bins, {"alden-cli": "src/main.rs", "openkakao-cli": "src/legacy_main.rs"})
        self.assertIn('include!("main.rs")', (ROOT / bins["openkakao-cli"]).read_text())

    def test_bundle_declares_both_exact_paths_and_preserves_main_identity(self):
        config = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text())
        self.assertEqual(config["identifier"], "com.openkakao.alden.desktop")
        for name in ("alden-cli", "openkakao-cli"):
            self.assertEqual(config["bundle"]["resources"][f"bundle-resources/bin/{name}"], f"bin/{name}")
        self.assertTrue(all("*" not in value for value in config["bundle"]["resources"].values()))


if __name__ == "__main__":
    unittest.main()
