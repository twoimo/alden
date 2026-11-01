# Runtime integration candidate

This branch combines the exact changes from draft PRs 33, 34, and 35 on frozen
`3407c08e8e3bdf14b378735bb7048e4398dbaede`: bare-digit lexical recall, serialized
annotation-review publication, and realtime target traversal past 200 inputs.
The original approved review-plan/source bundle remains immutable; this branch
needs its own delta review and installation evidence.

The renderer already suppresses passive topic, title, and saved-list captions at
the fitted overview. Two additional render-path tests now pin the reported
career labels: no passive labels from 0.3x through 1.47x, visible labels at 1.85x
and 2x, then no lingering labels on returning to 1x or farther. Keyboard entries
remain present. These tests use synthetic geometry and the real renderer methods,
not a native screenshot or installed-app acceptance. Explicit selection/hover
retains its separate reading behavior. Source changes are not installation proof.

Building the integrated source exposed a macOS loader failure in release
build-time macro libraries. A cloned library failed with a misaligned LINKEDIT
string table; a fresh target reproduced the failure, so this is not attributed
solely to stale/corrupt cache bytes. The actual dependency graph built with
`profile.release.build-override.debug=true` succeeded. Its newly built
`zerofrom_derive` library loaded, with an 8-byte-aligned string-table offset; the
default-profile library remained rejected with offset modulo 8 equal to 4.

The Tauri manifest records that build-only override so the canonical build's
input digest includes it. No global Rust configuration, compiler pin, permissions,
model, or installed app is changed. The smaller isolated macro fixture passed
under both profiles; it is explicitly not the failure reproducer. These local
observations are consistent with the [upstream Rust loader defect](https://github.com/rust-lang/rust/issues/157750).
Cargo documents the scope of [build-dependency profile overrides](https://doc.rust-lang.org/cargo/reference/profiles.html#build-dependencies).
This does not establish a universal compiler fix or a runtime performance gain.

Canonical build receipt/signature/resource verification, background native QA,
current-source external review, installation, and production observation remain
distinct gates. Test and build receipts live in the task's private
`outputs/runtime-integration-20261024/` directory. Nothing here certifies the full
Alden objective, messenger ingestion coverage, semantic gold, or normal release.
