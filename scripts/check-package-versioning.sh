#!/bin/sh
# Verify that every package version/release has a matching changelog entry.
set -eu

BASE="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
status=0

for makefile in "$BASE"/packages/*/Makefile; do
    package_dir="$(dirname "$makefile")"
    package="$(basename "$package_dir")"
    version="$(sed -n 's/^PKG_VERSION:=//p' "$makefile")"
    release="$(sed -n 's/^PKG_RELEASE:=//p' "$makefile")"
    changelog="$package_dir/CHANGELOG.md"
    if [ -z "$version" ] || [ -z "$release" ] || [ ! -r "$changelog" ]; then
        echo "missing package metadata or changelog: $package" >&2
        status=1
        continue
    fi
    if ! grep -q "^## ${version}-${release} " "$changelog"; then
        echo "$package: missing CHANGELOG.md entry for ${version}-${release}" >&2
        status=1
    fi
done

exit "$status"
