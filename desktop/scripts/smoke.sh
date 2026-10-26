#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
npm test
npm run build
cargo build --manifest-path ../Cargo.toml --bin alden-cli --bin openkakao-cli
cargo test --manifest-path src-tauri/Cargo.toml
cargo check --manifest-path src-tauri/Cargo.toml
