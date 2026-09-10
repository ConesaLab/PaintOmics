#!/usr/bin/env bash
# Build the PaintOmics application image.
#
#   ./deploy/build-image.sh [extra docker compose build args...]
#
# Packs the application tree into deploy/app.tar first, because the Dockerfile
# copies that single archive rather than the directories. See the comment above
# `COPY deploy/app.tar` in deploy/Dockerfile for why: a directory COPY of
# PaintomicsServer corrupts the image rootfs on the deployment host.
#
# tar also preserves the repository's symlinks natively -- including the cyclic
# src/src -> ../src and src/public_html, which points outside the tree -- so
# they need no special handling.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

ARCHIVE="deploy/app.tar"

for required in PaintomicsServer PaintomicsClient; do
    [ -d "${required}" ] || { echo "missing ${required}/ -- run from a full checkout" >&2; exit 1; }
done

echo "packing ${ARCHIVE}"
rm -f "${ARCHIVE}"

# --exclude runs before archiving, so nothing sensitive or generated is packed.
# serverconf.py in particular holds live credentials and must never be baked
# into an image; the container generates it from the template at start-up.
tar -cf "${ARCHIVE}" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.DS_Store' \
    --exclude='._*' \
    --exclude='PaintomicsServer/src/conf/serverconf.py' \
    --exclude='PaintomicsServer/src/conf/local_serverconf.py' \
    --exclude='node_modules' \
    --exclude='PaintomicsServer/src/log' \
    PaintomicsServer PaintomicsClient

echo "  $(du -h "${ARCHIVE}" | cut -f1), $(tar -tf "${ARCHIVE}" | wc -l | tr -d ' ') entries"

# Fail loudly if a credential slipped in, rather than shipping it.
if tar -tf "${ARCHIVE}" | grep -qE 'conf/(local_)?serverconf\.py$'; then
    echo "REFUSING TO BUILD: serverconf.py is inside ${ARCHIVE}" >&2
    exit 1
fi

# The Rust MORE port ships as a platform-specific binary beside runMORE.R, and
# it is gitignored so each deployment drops in the build it needs. Two ways that
# goes wrong without a word:
#
#   * a developer's macOS build (Mach-O arm64) gets packed into a Linux image,
#     where it fails at exec. _resolveMOREBackend then falls back to R for every
#     PLS1 job, which on a host with no MORE package means the analysis simply
#     stops working -- and the only symptom is that it got slower, or dead.
#   * `git archive` DROPS this file because it is gitignored, while the tar
#     above packs it from the working tree. The two delivery paths therefore
#     disagree about whether the binary is even present.
#
# Absent is a legitimate state -- it means every job goes to R, exactly as
# before the port existed -- so absence is reported, not punished. Present but
# built for the wrong machine is never legitimate.
MORE_RS="PaintomicsServer/src/common/bioscripts/more-rs"
if [ -e "${MORE_RS}" ]; then
    # Read the ELF header directly rather than shelling out to `file`, which is
    # not guaranteed on a build host. Bytes 0-3 are the magic, byte 4 is
    # EI_CLASS (02 = 64-bit) and bytes 18-19 are e_machine, little-endian:
    # 0x3e = x86-64, 0xb7 = aarch64.
    header="$(od -An -tx1 -N19 "${MORE_RS}" | tr -d ' \n')"
    # Match the image docker will actually build, so an arm64 laptop building
    # for itself is fine and only a genuine mismatch fails.
    case "$(docker version --format '{{.Server.Arch}}' 2>/dev/null)" in
        arm64|aarch64) want_machine="b7"; want_name="arm64" ;;
        *)             want_machine="3e"; want_name="amd64" ;;
    esac
    case "${header}" in
        7f454c4602*"${want_machine}")
            echo "  more-rs: ELF 64-bit ${want_name}, matches the image" ;;
        7f454c46*)
            echo "REFUSING TO BUILD: ${MORE_RS} is an ELF binary for the wrong" >&2
            echo "  architecture -- the image is ${want_name}. Header: ${header}" >&2
            exit 1 ;;
        *)
            echo "REFUSING TO BUILD: ${MORE_RS} is not a Linux ELF binary." >&2
            echo "  Header: ${header} (a macOS Mach-O build starts cffaedfe)" >&2
            echo "  Cross-build one with:" >&2
            echo "    cargo build --release --target x86_64-unknown-linux-musl" >&2
            exit 1 ;;
    esac
else
    echo "  more-rs: absent -- every MORE job will run on R"
fi

# The symlinks are load-bearing; verify tar kept them as links.
#
# Listed once into a variable rather than piped per link, for two reasons. The
# archive is ~290 MB, so this walked it three times. And `tar ... | grep -q`
# cannot work under the `set -o pipefail` above: grep -q exits at the first
# match, tar takes SIGPIPE on the closed pipe and exits non-zero, and pipefail
# reports the pipeline as failed. A miss is equally fatal, because then grep
# itself exits 1. So the check warned on every build whatever the archive
# contained - it announced all three links missing on a build where all three
# were present and correct, which is the same as having no check at all.
listing="$(tar -tvf "${ARCHIVE}")"
missing=0
for link in PaintomicsServer/src/src \
            PaintomicsServer/src/AdminTools/src \
            PaintomicsServer/src/AdminTools/scripts/src; do
    case "${listing}" in
        *" ${link} -> "*) ;;
        *) echo "  WARNING: ${link} not stored as a symlink" >&2; missing=1 ;;
    esac
done
[ "${missing}" -eq 0 ] && echo "  symlinks preserved"

# Build through a `docker-container` buildkit rather than the one embedded in
# dockerd. On this host (Docker 29.7.1 / buildx 0.36.1 / overlay2) the embedded
# builder writes an image whose rootfs is missing /bin/sh while still reporting
# success: 3/3 builds on 2026-09-10 produced a 1.4 GB image that could not be
# started ("exec: /bin/sh: no such file or directory"), against 2.8 GB for the
# image the same tree produced on 2026-09-08. Layers 1-7 of the bad image --
# including the base layer that carries /bin/sh -- were byte-identical to the
# good one, so the damage is in how the later diffs are generated and exported,
# not in anything the Dockerfile does. Ruled out: daemon restart, provenance and
# SBOM attestations, build-context size, base-image corruption, and COPY
# ordering (the constraint the Dockerfile documents is intact).
#
# The docker-container driver runs its own buildkit with its own snapshotter and
# hands dockerd a finished tarball, which comes out sound.
BUILDER_NAME="paintomics-isolated"
if ! docker buildx inspect "${BUILDER_NAME}" >/dev/null 2>&1; then
    echo "creating isolated builder ${BUILDER_NAME}"
    docker buildx create --name "${BUILDER_NAME}" --driver docker-container >/dev/null
fi

echo "building image"
docker buildx build --builder "${BUILDER_NAME}" --load --provenance=false \
    -f deploy/Dockerfile -t paintomics-app:latest "$@" .

# A build that exits 0 is not evidence the image can run -- that is exactly the
# failure above. Probe it before anything recreates a container from it.
echo "probing the built image"
if ! docker run --rm --entrypoint /bin/sh paintomics-app:latest \
        -c 'ls /bin/sh /usr/bin/ls /usr/local/bin/entrypoint.sh >/dev/null' 2>/dev/null; then
    echo "REFUSING TO SHIP: the built image has no usable rootfs." >&2
    echo "Do not recreate the container -- the running one is still on the old image." >&2
    exit 1
fi
echo "  rootfs OK"

echo "done"
