#!/bin/sh
# Bake ui/qml/shaders/*.frag into the .qsb files QML loads at runtime.
#
# Qt 6 cannot compile GLSL at runtime, so the baked artifacts are committed:
# users get working shaders without qsb (which ships with Qt's dev tools, not
# the runtime). Re-run after editing any .frag.
set -e
qsb=$(command -v qsb || echo /usr/lib/qt6/bin/qsb)
[ -x "$qsb" ] || { echo "qsb not found (install qt6-shadertools)" >&2; exit 1; }
cd "$(dirname "$0")/../ui/qml/shaders"
for frag in *.frag; do
    "$qsb" --qt6 -o "$frag.qsb" "$frag"
    echo "baked $frag -> $frag.qsb"
done
