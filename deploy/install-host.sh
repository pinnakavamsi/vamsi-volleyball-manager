#!/usr/bin/env bash
# Run on the target VM as root. Never run this on the development Mac.
set +x
set -euo pipefail
umask 077

VM_ROOT=/opt/volleyball-manager
DEPLOY_USER=volleyball-deploy
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEPLOY_KEY=
fail() { printf 'Host preparation stopped: %s\n' "$*" >&2; exit 1; }

while (($#)); do
    case "$1" in
        --deploy-key) (($# >= 2)) || fail '--deploy-key requires a public key file'; DEPLOY_KEY=$2; shift 2 ;;
        *) fail "Unknown argument: $1" ;;
    esac
done
[[ ${EUID} -eq 0 ]] || fail 'Run with sudo on the target VM.'
[[ -r /etc/os-release ]] || fail 'Only supported Ubuntu/Debian VMs can use this installer.'
# shellcheck disable=SC1091
source /etc/os-release
case "${ID:-}:${VERSION_ID:-}" in
    ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|debian:12|debian:13) ;;
    *) fail "Unsupported OS ${ID:-unknown} ${VERSION_ID:-unknown}; no packages were changed." ;;
esac
[[ -d /run/systemd/system ]] || fail 'This installer requires a VM running systemd.'
case "$(dpkg --print-architecture)" in
    amd64|arm64) ;;
    *) fail 'The deployment image supports amd64/arm64 VMs; no packages were changed.' ;;
esac
command -v ss >/dev/null || fail 'Install iproute2 first; listener inspection is required.'
command -v flock >/dev/null || fail 'Install util-linux first; deployment locking is required.'
for input_file in receive-release.sh ssh-command.sh docker/10-init-roles.sh; do
    [[ -f "$SCRIPT_DIR/$input_file" && ! -L "$SCRIPT_DIR/$input_file" ]] || fail "Missing project deployment file: $input_file"
done
[[ ! -L "$VM_ROOT" ]] || fail "$VM_ROOT must not be a symlink."
if [[ -e "$VM_ROOT" ]]; then
    [[ -d "$VM_ROOT" && $(stat -c %u "$VM_ROOT") -eq 0 ]] || fail "$VM_ROOT must be a root-owned project directory."
fi
for directory in deploy deploy/docker releases config state data deploy-user deploy-user/.ssh; do
    [[ ! -L "$VM_ROOT/$directory" ]] || fail "$VM_ROOT/$directory must not be a symlink."
    if [[ -e "$VM_ROOT/$directory" ]]; then
        [[ -d "$VM_ROOT/$directory" && $(stat -c %u "$VM_ROOT/$directory") -eq 0 ]] || fail "$VM_ROOT/$directory must be a root-owned directory."
    fi
done

# Check before installing packages, which may start their services automatically.
while IFS= read -r listener; do
    [[ -z "$listener" ]] && continue
    [[ "$listener" == *'"caddy"'* ]] || fail "Port 80/443 is already used by another service: $listener"
done < <(ss -H -ltnp '( sport = :80 or sport = :443 )')
while IFS= read -r listener; do
    [[ -z "$listener" ]] && continue
    [[ "$listener" == *'"caddy"'* ]] || fail "UDP port 443 is already used by another service: $listener"
done < <(ss -H -lunp '( sport = :443 )')

installed() { [[ $(dpkg-query -W -f='${Status}' "$1" 2>/dev/null || true) == 'install ok installed' ]]; }
for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
    installed "$package" && fail "Conflicting package $package is installed. Review its workloads before replacing it; this script never removes packages."
done
existing_docker=false
if command -v docker >/dev/null; then
    installed docker-ce || fail 'Existing Docker is not the official docker-ce package; preserve it and review manually.'
    existing_docker=true
fi
existing_caddy=false
if command -v caddy >/dev/null; then
    existing_caddy=true
    [[ -f /etc/caddy/Caddyfile && ! -L /etc/caddy/Caddyfile ]] || fail 'Existing Caddy does not have a regular /etc/caddy/Caddyfile; review its configuration manually.'
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null || fail 'Existing Caddyfile is invalid; it was preserved.'
    [[ $(systemctl show -p ExecStart --value caddy.service) == *'--config /etc/caddy/Caddyfile'* ]] || fail 'Existing Caddy service uses a different configuration; it was preserved.'
    if systemctl is-active --quiet caddy-api.service; then
        fail 'caddy-api is active; file-based site installation requires operator review.'
    fi
fi
if getent passwd "$DEPLOY_USER" >/dev/null; then
    [[ $(getent passwd "$DEPLOY_USER" | cut -d: -f6) == "$VM_ROOT/deploy-user" ]] || fail "$DEPLOY_USER already exists with another home directory."
    for group in $(id -nG "$DEPLOY_USER"); do
        case "$group" in docker|sudo|wheel|admin) fail "$DEPLOY_USER already has privileged group $group; review this account manually." ;; esac
    done
fi
if [[ -n "$DEPLOY_KEY" ]]; then
    [[ -f "$DEPLOY_KEY" && ! -L "$DEPLOY_KEY" ]] || fail 'Deployment public key must be a regular file.'
fi
[[ ! -L /etc/sudoers.d/volleyball-manager ]] || fail 'The existing deployment sudoers file is a symlink; it was preserved.'
install -d -o root -g root -m 0755 "$VM_ROOT"
exec 9>"$VM_ROOT/.deployment.lock"
flock -n 9 || fail 'Another deployment or configuration is in progress.'

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl gnupg python3 sudo openssh-client
work_dir=$(mktemp -d /run/volleyball-host.XXXXXX)
install_stage=''
sudoers_stage=''
cleanup() {
    rm -rf -- "$work_dir"
    [[ -z "$install_stage" ]] || rm -rf -- "$install_stage"
    [[ -z "$sudoers_stage" ]] || rm -f -- "$sudoers_stage"
}
trap cleanup EXIT

if [[ "$existing_docker" == false ]] || ! docker compose version >/dev/null 2>&1; then
    # Preserve a pre-existing official repository definition and its signing key.
    if ! python3 - <<'PY'
from pathlib import Path
files = [Path('/etc/apt/sources.list'), *Path('/etc/apt/sources.list.d').glob('*.list'), *Path('/etc/apt/sources.list.d').glob('*.sources')]
raise SystemExit(0 if any(p.is_file() and 'download.docker.com/linux/' in p.read_text() for p in files) else 1)
PY
    then
        [[ ! -e /etc/apt/sources.list.d/volleyball-docker.sources ]] || fail 'An unexpected volleyball-docker.sources already exists; it was preserved.'
        [[ ! -e /etc/apt/keyrings/volleyball-docker.asc ]] || fail 'An unexpected volleyball-docker.asc already exists; it was preserved.'
        install -d -m 0755 /etc/apt/keyrings
        curl --fail --silent --show-error --location "https://download.docker.com/linux/$ID/gpg" -o "$work_dir/docker.asc"
        install -m 0644 -o root -g root "$work_dir/docker.asc" /etc/apt/keyrings/volleyball-docker.asc
        cat > "$work_dir/docker.sources" <<EOF
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: ${UBUNTU_CODENAME:-${VERSION_CODENAME}}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/volleyball-docker.asc
EOF
        install -m 0644 -o root -g root "$work_dir/docker.sources" /etc/apt/sources.list.d/volleyball-docker.sources
    fi
    apt-get update
    if [[ "$existing_docker" == false ]]; then
        apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    else
        apt-get install -y docker-compose-plugin
    fi
fi

if [[ "$existing_caddy" == false ]]; then
    if [[ -e /etc/caddy/Caddyfile ]]; then
        [[ -f /etc/caddy/Caddyfile && ! -L /etc/caddy/Caddyfile ]] || fail 'An existing nonstandard Caddyfile was preserved.'
    else
        # Avoid the package's default public welcome site during preparation.
        install -d -m 0755 /etc/caddy /etc/caddy/sites-enabled
        printf '# Local site files are installed separately.\nimport /etc/caddy/sites-enabled/*.caddy\n' > "$work_dir/Caddyfile"
        install -m 0644 -o root -g root "$work_dir/Caddyfile" /etc/caddy/Caddyfile
    fi
    if ! python3 - <<'PY'
from pathlib import Path
files = [Path('/etc/apt/sources.list'), *Path('/etc/apt/sources.list.d').glob('*.list'), *Path('/etc/apt/sources.list.d').glob('*.sources')]
raise SystemExit(0 if any(p.is_file() and 'dl.cloudsmith.io/public/caddy/stable' in p.read_text() for p in files) else 1)
PY
    then
        [[ ! -e /etc/apt/sources.list.d/volleyball-caddy.list ]] || fail 'An unexpected volleyball-caddy.list already exists; it was preserved.'
        [[ ! -e /usr/share/keyrings/caddy-stable-archive-keyring.gpg ]] || fail 'A Caddy signing key already exists without a matching repository; review it manually.'
        apt-get install -y --no-install-recommends debian-keyring debian-archive-keyring apt-transport-https
        curl --fail --silent --show-error --location https://dl.cloudsmith.io/public/caddy/stable/gpg.key -o "$work_dir/caddy.key"
        gpg --batch --dearmor --output "$work_dir/caddy.gpg" "$work_dir/caddy.key"
        install -m 0644 -o root -g root "$work_dir/caddy.gpg" /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl --fail --silent --show-error --location https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt -o "$work_dir/caddy.list"
        install -m 0644 -o root -g root "$work_dir/caddy.list" /etc/apt/sources.list.d/volleyball-caddy.list
    fi
    apt-get update
    apt-get -o Dpkg::Options::=--force-confold install -y caddy
fi

systemctl enable --now docker.service
docker info >/dev/null || fail 'Docker daemon is not reachable.'
docker compose version
systemctl enable --now caddy.service

install -d -o root -g root -m 0755 "$VM_ROOT" "$VM_ROOT/deploy" "$VM_ROOT/deploy/docker" "$VM_ROOT/releases"
install -d -o root -g root -m 0700 "$VM_ROOT/config" "$VM_ROOT/state" "$VM_ROOT/data"
[[ ! -L "$VM_ROOT/data/instance" ]] || fail 'Persistent instance directory must not be a symlink.'
install -d -o 10001 -g 10001 -m 0700 "$VM_ROOT/data/instance"
if ! getent passwd "$DEPLOY_USER" >/dev/null; then
    # An invalid password hash disables password login without the account-lock
    # prefix that can also block public keys on servers with UsePAM disabled.
    useradd --system --user-group --password '*' --home-dir "$VM_ROOT/deploy-user" --shell /bin/bash "$DEPLOY_USER"
fi
install -d -o root -g root -m 0755 "$VM_ROOT/deploy-user" "$VM_ROOT/deploy-user/.ssh"
install_stage=$(mktemp -d "$VM_ROOT/deploy/.install.XXXXXX")
for project_file in receive-release.sh ssh-command.sh; do
    install -o root -g root -m 0755 "$SCRIPT_DIR/$project_file" "$install_stage/$project_file"
    mv -f -- "$install_stage/$project_file" "$VM_ROOT/deploy/$project_file"
done
install -o root -g root -m 0755 "$SCRIPT_DIR/docker/10-init-roles.sh" "$install_stage/10-init-roles.sh"
mv -f -- "$install_stage/10-init-roles.sh" "$VM_ROOT/deploy/docker/10-init-roles.sh"
cat > "$work_dir/sudoers" <<'EOF'
# The receiver validates exactly two hex arguments and reads a bounded archive.
volleyball-deploy ALL=(root) NOPASSWD: /opt/volleyball-manager/deploy/receive-release.sh *
EOF
visudo -cf "$work_dir/sudoers" >/dev/null || fail 'Deployment sudoers validation failed.'
if [[ -e /etc/sudoers.d/volleyball-manager ]] && ! cmp -s "$work_dir/sudoers" /etc/sudoers.d/volleyball-manager; then
    fail 'An existing volleyball-manager sudoers policy differs; it was preserved.'
fi
sudoers_stage=$(mktemp /etc/sudoers.d/.volleyball-manager.XXXXXX)
install -o root -g root -m 0440 "$work_dir/sudoers" "$sudoers_stage"
mv -f -- "$sudoers_stage" /etc/sudoers.d/volleyball-manager
sudoers_stage=''

if [[ -n "$DEPLOY_KEY" ]]; then
    ssh-keygen -lf "$DEPLOY_KEY" >/dev/null || fail 'The supplied public key is invalid.'
    python3 - "$DEPLOY_KEY" "$VM_ROOT/deploy-user/.ssh/authorized_keys" <<'PY'
import os
from pathlib import Path
import sys
key = Path(sys.argv[1]).read_text().strip()
parts = key.split()
allowed = {'ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521'}
if '\n' in key or len(parts) < 2 or parts[0] not in allowed:
    raise SystemExit('Supply one ordinary public key without existing authorized_keys options.')
line = f'command="/opt/volleyball-manager/deploy/ssh-command.sh",restrict {parts[0]} {parts[1]} volleyball-github-deploy'
path = Path(sys.argv[2])
if path.is_symlink():
    raise SystemExit('Existing authorized_keys is a symlink; it was preserved.')
text = path.read_text() if path.exists() else ''
for existing in text.splitlines():
    if parts[1] in existing.split() and existing != line:
        raise SystemExit('This public key already has different authorization options; review it manually.')
if line not in text.splitlines():
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, 'a') as handle:
        handle.write(('\n' if text and not text.endswith('\n') else '') + line + '\n')
os.chown(path, 0, 0)
os.chmod(path, 0o644)
PY
fi

printf 'Docker Engine, Compose, and native Caddy are installed. Existing sites and SSH settings were preserved.\n'
printf 'Deployment account: %s (forced release command; no Docker group).\n' "$DEPLOY_USER"
printf 'Next: run deploy/configure.sh --domain YOUR_HOSTNAME as root to generate infrastructure secrets and install your site.\n'
printf 'Firewall rules were preserved. Keep the current SSH port reachable; the domain needs TCP 80/443.\n'
