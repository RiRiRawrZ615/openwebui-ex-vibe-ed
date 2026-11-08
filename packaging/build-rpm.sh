#!/usr/bin/env bash
# Build the Open Web Vibes RPM from the committed tree. The frontend must be built first; the
# package ships a prebuilt build/, so node is build-time only.
set -Eeuo pipefail

ROOT="$(dirname "$(readlink -f "$0")")"
REPO="$(dirname "$ROOT")"
VERSION="${OWV_RPM_VERSION:-1.0.0}"
RELEASE="${OWV_RPM_RELEASE:-1.rc1}"
BUILDDIR="${OWV_RPM_BUILDDIR:-$ROOT/rpmbuild}"

if [ ! -d "$REPO/build" ]; then
    echo "[ERROR] no prebuilt frontend at $REPO/build. Run npm run build first." >&2
    exit 1
fi

mkdir -p "$BUILDDIR"/SPECS "$BUILDDIR"/SOURCES "$BUILDDIR"/BUILD "$BUILDDIR"/RPMS "$BUILDDIR"/BUILDROOT

tar -C "$REPO" --exclude=.git --transform 's#^#openwebvibes/#' \
    -cf "$BUILDDIR/SOURCES/openwebvibes.tar.gz" backend build packaging tests

cp "$ROOT/openwebvibes.spec" "$BUILDDIR/SPECS/openwebvibes.spec"
rpmbuild -bb \
    --define "_topdir $BUILDDIR" \
    --define "_version $VERSION" \
    --define "_release $RELEASE" \
    "$BUILDDIR/SPECS/openwebvibes.spec"

echo
find "$BUILDDIR/RPMS" -name '*.rpm' -type f
