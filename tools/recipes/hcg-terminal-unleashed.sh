#!/usr/bin/env bash
# Build recipe for HCG Terminal Unleashed: runs the project's own documented packaging command.
# Usage: hcg-terminal-unleashed.sh <source-checkout> <output-dir> <rootless|rootful> <entitlements-path-in-source>
# Requires macOS with Xcode, ldid and dpkg. Produces exactly one .deb in <output-dir>.
set -euo pipefail
src="$1"; out="$2"; layout="$3"; ent="$4"
case "$layout" in rootless|rootful) ;; *) echo "bad layout $layout" >&2; exit 2 ;; esac
[ -f "$src/$ent" ] || { echo "entitlements file $ent not found in source" >&2; exit 2; }
[ -x "$src/Scripts/package-ios.sh" ] || { echo "Scripts/package-ios.sh missing in source" >&2; exit 2; }
mkdir -p "$out"
work="$(mktemp -d)"
(cd "$src" && Scripts/package-ios.sh unleashed "$work/dist" --deb "$layout" --ldid-entitlements "$src/$ent")
debs=("$work"/dist/unleashed/*.deb)
[ "${#debs[@]}" -eq 1 ] && [ -f "${debs[0]}" ] || { echo "expected exactly one .deb, got ${#debs[@]}" >&2; exit 1; }
cp "${debs[0]}" "$out/"
echo "== built $(basename "${debs[0]}")"
dpkg-deb -I "$out/$(basename "${debs[0]}")"
shasum -a 256 "$out/$(basename "${debs[0]}")"
