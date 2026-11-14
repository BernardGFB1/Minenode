#!/bin/sh
# Build PicoLimbo with MineNode's /login + /register module.
#
#   sh picolimbo/build.sh <upstream-tag> <rust-target> <output-dir>
#   e.g. sh picolimbo/build.sh v1.15.0+mc26.3 x86_64-unknown-linux-musl out
#
# The upstream source is taken unchanged from https://github.com/Quozul/PicoLimbo at the given tag;
# minenode-auth.patch adds the login module (pico_limbo/src/auth.rs) and the command handling.
# The two crates it needs are added with `cargo add`, so upstream version bumps in Cargo.toml
# never break the patch. Used by .github/workflows/picolimbo.yml; works locally with rustup.
set -eu

TAG=${1:?upstream tag, e.g. v1.15.0+mc26.3}
TARGET=${2:?rust target, e.g. x86_64-unknown-linux-musl}
OUT=${3:?output directory}

HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

git clone --quiet --depth 1 --branch "$TAG" https://github.com/Quozul/PicoLimbo.git "$WORK/src"
cd "$WORK/src"
git apply --whitespace=nowarn "$HERE/minenode-auth.patch"
cargo add --quiet --package pico_limbo hex@0.4 getrandom@0.3
rustup target add "$TARGET"   # the pinned toolchain from rust-toolchain.toml is installed on first use
cargo build --release --target "$TARGET" --bin pico_limbo
cp "target/$TARGET/release/pico_limbo" "$OUT/pico_limbo"
echo "Built $OUT/pico_limbo (PicoLimbo $TAG + MineNode login, $TARGET)"
