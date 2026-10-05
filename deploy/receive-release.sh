#!/usr/bin/env bash
# Installed once as a root-owned script. Receives a Git archive on stdin.
set -euo pipefail
umask 077
readonly VM_ROOT=/opt/volleyball-manager
[[ "$EUID" -eq 0 ]] || { echo 'Run the installed receiver through sudo.' >&2; exit 1; }
[[ "$#" -eq 2 && "$1" =~ ^[0-9a-f]{40}$ && "$2" =~ ^[0-9a-f]{64}$ ]] || { echo 'Expected a full commit SHA and archive SHA-256.' >&2; exit 1; }
readonly revision="$1" expected_digest="$2"
install -d -o root -g root -m 0755 "$VM_ROOT/releases"
exec 8> "$VM_ROOT/.intake.lock"
flock -w 900 8 || { echo 'Another source upload is still running.' >&2; exit 1; }
archive="$(mktemp "$VM_ROOT/releases/.archive-${revision}.XXXXXX")"
stage=''
cleanup() { rm -f -- "$archive" "$archive.tar"; [[ -z "$stage" ]] || rm -rf -- "$stage"; }
trap cleanup EXIT
# Read at most 100 MiB plus one byte, so a compressed upload cannot fill the disk.
timeout 180 head -c 104857601 > "$archive"
[[ "$(stat -c '%s' "$archive")" -le 104857600 ]] || { echo 'Source archive exceeds 100 MiB.' >&2; exit 1; }
actual_digest="$(sha256sum "$archive" | cut -d ' ' -f 1)"
[[ "$actual_digest" == "$expected_digest" ]] || { echo 'Source archive checksum does not match.' >&2; exit 1; }
release="$VM_ROOT/releases/$revision"
if [[ -e "$release" ]]; then
  [[ -d "$release" && ! -L "$release" && -f "$release/.archive-sha256" ]] || { echo 'An invalid existing release blocks this commit.' >&2; exit 1; }
  [[ "$(cat "$release/.archive-sha256")" == "$expected_digest" ]] || { echo 'This commit already has a different immutable archive.' >&2; exit 1; }
else
  stage="$(mktemp -d "$VM_ROOT/releases/.extract-${revision}.XXXXXX")"
  python3 - "$archive" "$stage" <<'PY'
import gzip, os, pathlib, shutil, sys, tarfile

archive, destination = sys.argv[1:]
root = pathlib.Path(destination)
seen, members, total = set(), [], 0
# Bound decompression before tarfile parses potentially large PAX headers.
expanded = pathlib.Path(archive + '.tar')
expanded_size = 0
with gzip.open(archive, 'rb') as compressed, expanded.open('xb') as output:
    while chunk := compressed.read(1048576):
        expanded_size += len(chunk)
        if expanded_size > 268435456:
            raise SystemExit('Archive expands beyond the source upload limit.')
        output.write(chunk)
with tarfile.open(expanded, 'r:') as bundle:
    for member in bundle:
        name = member.name.rstrip('/')
        path = pathlib.PurePosixPath(name)
        if not name or path.is_absolute() or any(part in ('', '.', '..') for part in name.split('/')):
            raise SystemExit('Archive contains an unsafe path.')
        if name in seen or not (member.isdir() or member.isfile()):
            raise SystemExit('Archive contains duplicate paths, links, or special files.')
        if path.parts[0] in ('.git', 'config', 'data', '.archive-sha256') or (path.parts[0] == 'instance' and name not in ('instance', 'instance/.gitkeep')):
            raise SystemExit('Archive contains private deployment or runtime paths.')
        if path.parts[0].startswith('.env') and name != '.env.example':
            raise SystemExit('Archive contains an environment file.')
        total += member.size
        if len(members) >= 20000 or member.size > 67108864 or total > 268435456:
            raise SystemExit('Archive expands beyond the source upload limits.')
        seen.add(name)
        members.append((member, path))
    for member, path in members:
        output = root.joinpath(*path.parts)
        if member.isdir():
            output.mkdir(parents=True, exist_ok=True)
            output.chmod(0o755)
            continue
        output.parent.mkdir(parents=True, exist_ok=True)
        source = bundle.extractfile(member)
        if source is None:
            raise SystemExit('Archive member is unreadable.')
        with source, output.open('xb') as target:
            shutil.copyfileobj(source, target)
        output.chmod(0o755 if member.mode & 0o111 else 0o644)
    for directory, _, _ in os.walk(root):
        pathlib.Path(directory).chmod(0o755)
PY
  [[ -f "$stage/Dockerfile" && -f "$stage/compose.yaml" && -f "$stage/deploy/release.sh" ]] || { echo 'Archive is missing deployment files.' >&2; exit 1; }
  printf '%s\n' "$expected_digest" > "$stage/.archive-sha256"
  chmod 0644 "$stage/.archive-sha256"
  mv -- "$stage" "$release"
  stage=''
fi
cleanup
trap - EXIT
flock -u 8
exec 8>&-
exec bash "$release/deploy/release.sh" "$revision"
