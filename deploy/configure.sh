#!/usr/bin/env bash
# Create secrets on the VM; never copy the development .env to the server.
set +x
set -euo pipefail
umask 077
VM_ROOT=/opt/volleyball-manager
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
fail() { printf 'Server configuration stopped: %s\n' "$*" >&2; exit 1; }
DOMAIN=
while (($#)); do
    case "$1" in
        --domain) (($# >= 2)) || fail '--domain requires the hostname you control'; DOMAIN=$2; shift 2 ;;
        *) fail "Unknown argument: $1" ;;
    esac
done
[[ -n "$DOMAIN" ]] || fail 'Supply --domain with your own HTTPS hostname.'
python3 - "$DOMAIN" <<'PY'
import re,sys
domain=sys.argv[1]
if len(domain)>253 or '.' not in domain or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',label) for label in domain.split('.')):
    raise SystemExit('Supply one valid DNS hostname without a scheme, port, path or wildcard.')
PY
[[ ${EUID} -eq 0 ]] || fail 'Run with sudo on the target VM.'
[[ -d /run/systemd/system ]] || fail 'Run this on the systemd VM, not the development machine.'
[[ -d "$VM_ROOT/config" && ! -L "$VM_ROOT/config" ]] || fail 'Run install-host.sh first.'
[[ $(stat -c %u "$VM_ROOT") -eq 0 && $(stat -c %u "$VM_ROOT/config") -eq 0 ]] || fail 'The project and configuration directories must be root-owned.'
[[ -f "$SCRIPT_DIR/Caddyfile" && ! -L "$SCRIPT_DIR/Caddyfile" ]] || fail 'The project Caddyfile is missing.'
[[ -f /etc/caddy/Caddyfile && ! -L /etc/caddy/Caddyfile ]] || fail 'A regular native Caddyfile is required.'
command -v caddy >/dev/null || fail 'Run install-host.sh first.'
command -v python3 >/dev/null || fail 'Run install-host.sh first.'
exec 9>/run/lock/volleyball-manager-config.lock
flock -n 9 || fail 'Another server configuration is in progress.'
exec 8>"$VM_ROOT/.deployment.lock"
flock -n 8 || fail 'Another application deployment or rollback is in progress.'

python3 - "$VM_ROOT" "$DOMAIN" <<'PY'
import base64
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile

root = Path(sys.argv[1])
domain = sys.argv[2].lower()
config = root / 'config'
names = ['runtime.env', 'operator.env', 'postgres.env', 'release.env']
existing = [name for name in names if (config / name).exists() or (config / name).is_symlink()]
if existing:
    if len(existing) != len(names):
        raise SystemExit('Configuration is incomplete. Existing secrets were preserved; restore the complete set or review it manually.')
    for name in names:
        path = config / name
        if path.is_symlink() or not path.is_file() or path.stat().st_uid != 0:
            raise SystemExit(f'{name} must be a regular root-owned file. It was preserved.')
        os.chmod(path, 0o600)
    public_url=next((line.split('=',1)[1].strip() for line in (config/'runtime.env').read_text().splitlines() if line.startswith('PUBLIC_BASE_URL=')),None)
    if public_url!=f'https://{domain}':
        raise SystemExit('The supplied domain differs from existing runtime configuration. Existing secrets/sites were preserved; review hostname changes manually.')
    print('Existing VM secrets were preserved; no passwords were regenerated.')
else:
    bootstrap, owner, app, backup = [secrets.token_hex(32) for _ in range(4)]
    database = lambda role, password: f'postgresql+psycopg://{role}:{password}@postgres:5432/volleyball'
    runtime = {
        'DATABASE_URL': database('volleyball_app', app),
        'APP_ENV': 'production',
        'PUBLIC_BASE_URL': f'https://{domain}',
        'SECRET_KEY': secrets.token_urlsafe(64),
        'MFA_ENCRYPTION_KEY': base64.urlsafe_b64encode(os.urandom(32)).decode(),
        'SESSION_COOKIE_SECURE': 'true',
        'TRUST_PROXY_HEADERS': 'true',
        'TRUSTED_HOSTS': f'{domain},localhost,127.0.0.1',
        'PRIVATE_STORAGE_PATH': '/app/instance/files',
        'GUNICORN_WORKERS': '1',
        'GUNICORN_THREADS': '4',
        'EMAIL_DELIVERY_MODE': 'deferred',
        'SMTP_HOST': '', 'SMTP_PORT': '587', 'SMTP_USER': '', 'SMTP_PASSWORD': '', 'SMTP_FROM': '',
        'GEOCODING_PROVIDER': '', 'GEOCODING_PROVIDER_URL': '', 'GEOCODING_LICENSE_CONFIRMED': 'false',
        'WEATHER_PROVIDER_URL': '', 'WEATHER_PROVIDER_NAME': '', 'WEATHER_LICENSE_CONFIRMED': 'false', 'WEATHER_PROVIDER_TOKEN': '',
        'BACKUP_STATUS_PATH': '',
    }
    operator = {
        'MIGRATION_DATABASE_URL': database('volleyball_owner', owner),
        'BACKUP_DATABASE_URL': database('volleyball_backup', backup),
        'BASEBACKUP_DATABASE_URL': database('volleyball_backup', backup),
    }
    postgres = {
        'POSTGRES_USER': 'postgres', 'POSTGRES_DB': 'postgres', 'POSTGRES_PASSWORD': bootstrap,
        'POSTGRES_HOST_AUTH_METHOD': 'scram-sha-256',
        'POSTGRES_INITDB_ARGS': '--auth-host=scram-sha-256 --auth-local=scram-sha-256 --data-checksums',
        'PGDATA': '/var/lib/postgresql/18/docker',
        'VOLLEYBALL_OWNER_PASSWORD': owner, 'VOLLEYBALL_APP_PASSWORD': app, 'VOLLEYBALL_BACKUP_PASSWORD': backup,
    }
    release = {'VM_ROOT': str(root), 'APP_IMAGE': 'volleyball-manager:bootstrap'}
    staging = Path(tempfile.mkdtemp(prefix='.configure-', dir=config))
    try:
        for name, values in zip(names, [runtime, operator, postgres, release], strict=True):
            path = staging / name
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as handle:
                handle.write('# Private server configuration. Do not commit or print this file.\n')
                handle.writelines(f'{key}={value}\n' for key, value in values.items())
                handle.flush()
                os.fsync(handle.fileno())
        for name in names:
            os.rename(staging / name, config / name)
    finally:
        shutil.rmtree(staging)
    print('Generated separate VM signing, MFA, bootstrap, migration, runtime, and backup credentials in private files.')
os.chown(config, 0, 0)
os.chmod(config, 0o700)
PY

# Only this site's managed file may be updated. All other Caddy sites survive.
site=/etc/caddy/sites-enabled/volleyball-manager.caddy
install -d -m 0755 -o root -g root /etc/caddy/sites-enabled
[[ ! -L "$site" ]] || fail 'The target Caddy site is a symlink; it was preserved.'
if [[ -e "$site" ]] && [[ $(head -n 1 "$site") != '# Managed by Volleyball Manager' ]]; then
    fail 'A different site already owns volleyball-manager.caddy; it was preserved.'
fi
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null || fail 'Existing Caddy configuration is invalid; it was preserved.'
backup_dir=$(mktemp -d /etc/caddy/.volleyball-config.XXXXXX)
cp -p /etc/caddy/Caddyfile "$backup_dir/Caddyfile.original"
site_existed=false
if [[ -f "$site" ]]; then site_existed=true; cp -p "$site" "$backup_dir/site.original"; fi
changed=false
committed=false
restore() {
    if [[ "$changed" == true && "$committed" == false ]]; then
        cp -p "$backup_dir/Caddyfile.original" "$backup_dir/Caddyfile.restore"
        mv -f -- "$backup_dir/Caddyfile.restore" /etc/caddy/Caddyfile
        if [[ "$site_existed" == true ]]; then
            cp -p "$backup_dir/site.original" "$backup_dir/site.restore"
            mv -f -- "$backup_dir/site.restore" "$site"
        else
            rm -f -- "$site"
        fi
    fi
    rm -rf -- "$backup_dir"
}
trap restore EXIT
cp -p /etc/caddy/Caddyfile "$backup_dir/Caddyfile.candidate"
python3 - "$backup_dir/Caddyfile.candidate" "$site" <<'PY'
import fnmatch
from pathlib import Path
import shlex
import sys
path, site = Path(sys.argv[1]), sys.argv[2]
text = path.read_text()
imported = False
for line in text.splitlines():
    try:
        tokens = shlex.split(line, comments=True)
    except ValueError:
        continue
    if len(tokens) == 2 and tokens[0] == 'import':
        pattern = tokens[1]
        if not pattern.startswith('/'):
            pattern = str(Path('/etc/caddy') / pattern)
        if fnmatch.fnmatchcase(site, pattern):
            imported = True
if not imported:
    text = text.rstrip() + '\n\n# Volleyball Manager site; other site definitions are preserved.\nimport ' + site + '\n'
path.write_text(text)
PY
changed=true
python3 - "$SCRIPT_DIR/Caddyfile" "$backup_dir/site.candidate" "$DOMAIN" <<'PY'
from pathlib import Path
import sys
source,target,domain=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3].lower()
template=source.read_text()
if template.count('{{DOMAIN}}')!=1:
    raise SystemExit('The Caddy site template requires exactly one hostname placeholder.')
target.write_text(template.replace('{{DOMAIN}}',domain))
PY
chmod 0644 "$backup_dir/site.candidate"
chown root:root "$backup_dir/site.candidate"
mv -f -- "$backup_dir/site.candidate" "$site"
# Validate at the real path so any existing relative imports keep their meaning.
mv -f -- "$backup_dir/Caddyfile.candidate" /etc/caddy/Caddyfile
if ! caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null; then
    fail 'Combined Caddy configuration failed validation; the original files are being restored.'
fi
if systemctl is-active --quiet caddy.service; then
    if ! systemctl reload caddy.service; then
        restore
        changed=false
        systemctl reload caddy.service || true
        fail 'Caddy reload failed; the original site configuration was restored.'
    fi
else
    systemctl start caddy.service || fail 'Caddy could not start; the original configuration is being restored.'
fi
committed=true
printf 'Private configuration is ready. Caddy proxies %s to 127.0.0.1:5050.\n' "$DOMAIN"
printf 'HTTPS issuance requires this hostname to resolve to the VM and TCP 80/443 to reach it.\n'
printf 'The app starts with the first release; this step does not claim public application readiness.\n'
