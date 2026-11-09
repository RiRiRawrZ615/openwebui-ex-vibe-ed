#!/usr/bin/env bash
# Build the Open Web Vibes RPM from the committed tree. The frontend must be built first; the
# package ships a prebuilt build/, so node is build-time only.
set -Eeuo pipefail

ROOT="$(dirname "$(readlink -f "$0")")"
REPO="$(dirname "$ROOT")"
VERSION="${OWV_RPM_VERSION:-1.0.0}"
RELEASE="${OWV_RPM_RELEASE:-1.rc1}"
BUILDDIR="${OWV_RPM_BUILDDIR:-$ROOT/rpmbuild}"

# OOM guard, before anything is written. A build killed mid-archive leaves a half-written tarball in
# BUILDDIR and the next build inherits it, which is how one failed build turns into a run of empty
# packages. Headroom counts free swap as well as free memory because zram is what is actually
# available on a machine that is already holding a loaded model.
MIN_FREE_MIB="${OWV_BUILD_MIN_FREE_MIB:-4096}"
SWAP_FILE="${OWV_BUILD_SWAP:-/var/tmp/owv-build-swap}"
SWAP_ADDED=0

headroom_mib() {
    free -b | awk '/^Mem:/ {a=$7} /^Swap:/ {s=$4} END {print int((a+s)/1048576)}'
}

release_swap() {
    if [ "$SWAP_ADDED" = "1" ]; then
        swapoff "$SWAP_FILE" 2>/dev/null || true
        rm -f "$SWAP_FILE" 2>/dev/null || true
        echo "[guard] temporary swap released"
    fi
}
trap release_swap EXIT

have="$(headroom_mib)"
if [ "${OWV_BUILD_NO_SWAP:-0}" != "1" ] && [ "${have:-0}" -lt "$MIN_FREE_MIB" ]; then
    need=$((MIN_FREE_MIB - have))
    echo "[guard] headroom ${have} MiB, floor ${MIN_FREE_MIB} MiB; trying ${need} MiB temporary swap"
    if command -v sudo >/dev/null 2>&1 && sudo truncate -s "${need}M" "$SWAP_FILE" 2>/dev/null \
        && sudo mkswap "$SWAP_FILE" >/dev/null 2>&1 && sudo swapon "$SWAP_FILE" 2>/dev/null; then
        SWAP_ADDED=1
        have="$(headroom_mib)"
        echo "[guard] headroom now ${have} MiB"
    else
        echo "[ERROR] headroom below the floor and no way to add swap. Stop the engine, or lower" >&2
        echo "        OWV_BUILD_MIN_FREE_MIB, or build with OWV_BUILD_NO_SWAP=1 and accept the risk." >&2
        exit 1
    fi
fi
echo "[guard] headroom ${have} MiB (floor ${MIN_FREE_MIB} MiB)"

if [ ! -d "$REPO/build" ]; then
    echo "[ERROR] no prebuilt frontend at $REPO/build. Run npm run build first." >&2
    exit 1
fi

mkdir -p "$BUILDDIR"/SPECS "$BUILDDIR"/SOURCES "$BUILDDIR"/BUILD "$BUILDDIR"/RPMS "$BUILDDIR"/BUILDROOT

# The build dir lives under packaging/, which is itself part of the payload, so the archive was
# writing into itself and came out empty. The tree being packed is excluded from its own archive.
tar -C "$REPO" --exclude=.git --exclude="$BUILDDIR" --transform 's#^#openwebvibes/#' \
    # CHANGELOG.md was not in the archive. env.py reads it at import time and its except branch calls
    # pkgutil.get_data, which also throws when the file is absent, so a packaged install died during
    # import and the launcher reported only "webui exited during startup". The file is now shipped and
    # the code guards the absence, both.
    -cf "$BUILDDIR/SOURCES/openwebvibes.tar.gz" backend build packaging tests CHANGELOG.md

cp "$ROOT/openwebvibes.spec" "$BUILDDIR/SPECS/openwebvibes.spec"
rpmbuild -bb \
    --define "_topdir $BUILDDIR" \
    --define "_version $VERSION" \
    --define "_release $RELEASE" \
    "$BUILDDIR/SPECS/openwebvibes.spec"

echo
find "$BUILDDIR/RPMS" -name '*.rpm' -type f
