#!/usr/bin/env bash
set -euo pipefail
readonly VM_ROOT=/opt/volleyball-manager
[[ "$EUID" -eq 0 ]] || { echo 'Run rollback as a VM operator with sudo.' >&2; exit 1; }
if [[ "$#" -eq 0 ]]; then
  [[ -f "$VM_ROOT/config/previous-release" ]] || { echo 'No previous release has been recorded.' >&2; exit 1; }
  revision="$(cat "$VM_ROOT/config/previous-release")"
elif [[ "$#" -eq 1 ]]; then
  revision="$1"
else
  echo 'Usage: rollback.sh [full-commit-SHA]' >&2; exit 1
fi
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected a full commit SHA.' >&2; exit 1; }
exec bash "$VM_ROOT/releases/$revision/deploy/release.sh" --rollback "$revision"
