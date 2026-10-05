#!/usr/bin/env bash
# Stable root-owned forced command for the dedicated deployment SSH key.
set +x
set -euo pipefail
command_text=${SSH_ORIGINAL_COMMAND:-}
pattern='^sudo -n /opt/volleyball-manager/deploy/receive-release\.sh ([0-9a-f]{40}) ([0-9a-f]{64})$'
if [[ ! "$command_text" =~ $pattern ]]; then
    printf 'This SSH key only accepts the Volleyball Manager release command.\n' >&2
    exit 1
fi
exec /usr/bin/sudo -n /opt/volleyball-manager/deploy/receive-release.sh "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
