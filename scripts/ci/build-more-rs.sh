#!/usr/bin/env bash
#
# Provide the more-rs binary (the Rust port of MORE) at the path the server
# looks for it, src/common/bioscripts/more-rs, building it from the pinned
# commit of github.com/TianYuan-Liu/MORE when the cached copy is absent.
#
# The regression baseline's MORE datasets were produced on this port (see
# scripts/regression.sh), so CI must run the same engine at the same commit.
#
# Usage: scripts/ci/build-more-rs.sh
# Env:   PAINTOMICS_CI_HOME  (default ~/paintomics-ci)
#        MORE_RS_COMMIT      (default: the pinned commit below)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CI_HOME="${PAINTOMICS_CI_HOME:-$HOME/paintomics-ci}"
MORE_RS_COMMIT="${MORE_RS_COMMIT:-8d9ffebb0dc387c81cd1df3dc9ea5958c0d5c1b0}"
CACHED="$CI_HOME/more-rs/$MORE_RS_COMMIT/more-rs"
TARGET="$ROOT/PaintomicsServer/src/common/bioscripts/more-rs"

if [ ! -x "$CACHED" ]; then
    echo "==> building more-rs at $MORE_RS_COMMIT"
    BUILD="$(mktemp -d)"
    git clone --quiet https://github.com/TianYuan-Liu/MORE.git "$BUILD/MORE"
    git -C "$BUILD/MORE" checkout --quiet "$MORE_RS_COMMIT"
    (cd "$BUILD/MORE/rust" && cargo build --release --quiet)
    mkdir -p "$(dirname "$CACHED")"
    cp "$BUILD/MORE/rust/target/release/more-rs" "$CACHED"
    rm -rf "$BUILD"
else
    echo "==> more-rs $MORE_RS_COMMIT from cache"
fi

cp "$CACHED" "$TARGET"
chmod +x "$TARGET"
"$TARGET" --help >/dev/null
echo "==> more-rs installed at $TARGET"
