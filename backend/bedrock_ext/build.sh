#!/usr/bin/env sh
# Build MineNodeRouter.jar against a Geyser standalone JAR.
# Usage: ./build.sh /path/to/geyser-standalone.jar [--test]
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
GEYSER_JAR=${1:-}
if [ -z "$GEYSER_JAR" ] || [ ! -f "$GEYSER_JAR" ]; then
    echo "Usage: $0 /path/to/geyser-standalone.jar [--test]" >&2
    exit 2
fi
RUN_TESTS=0
if [ "${2:-}" = "--test" ]; then
    RUN_TESTS=1
fi

BUILD="$ROOT/build"
CLASSES="$BUILD/classes"
rm -rf "$BUILD"
mkdir -p "$CLASSES"

find "$ROOT/src" -name '*.java' -print > "$BUILD/sources.txt"
javac --release 21 -encoding UTF-8 -Xlint:all -classpath "$GEYSER_JAR" -d "$CLASSES" @"$BUILD/sources.txt"
cp "$ROOT/extension.yml" "$CLASSES/"

OUT_JAR="$ROOT/MineNodeRouter.jar"
jar --create --file "$OUT_JAR" -C "$CLASSES" .
echo "Built $OUT_JAR"

if [ "$RUN_TESTS" -eq 1 ]; then
    TEST_CLASSES="$BUILD/test-classes"
    mkdir -p "$TEST_CLASSES"
    find "$ROOT/test" -name '*.java' -print > "$BUILD/test-sources.txt"
    javac --release 21 -encoding UTF-8 -classpath "$GEYSER_JAR:$CLASSES" -d "$TEST_CLASSES" @"$BUILD/test-sources.txt"
    java -classpath "$CLASSES:$TEST_CLASSES" pl.minenode.router.RouteTableTest
fi
