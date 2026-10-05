#!/usr/bin/env bash
# Build first, stop writes, migrate once, and switch the application image.
set -euo pipefail
umask 077
readonly VM_ROOT=/opt/volleyball-manager
export VM_ROOT
[[ "$EUID" -eq 0 ]] || { echo 'Release requires the root-owned deployment receiver.' >&2; exit 1; }
mode=deploy
if [[ "${1:-}" == '--rollback' ]]; then mode=rollback; shift; fi
[[ "$#" -eq 1 && "$1" =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected a full commit SHA.' >&2; exit 1; }
readonly revision="$1" release="$VM_ROOT/releases/$1" image="volleyball-manager:$1"
[[ -d "$release" && ! -L "$release" && -f "$release/.archive-sha256" ]] || { echo 'The immutable source release is unavailable.' >&2; exit 1; }
for file in runtime.env operator.env postgres.env release.env; do
  [[ -f "$VM_ROOT/config/$file" && ! -L "$VM_ROOT/config/$file" ]] || { echo "Missing private config/$file." >&2; exit 1; }
  [[ "$(stat -c '%u:%a' "$VM_ROOT/config/$file")" == '0:600' ]] || { echo "Private config/$file must be root-owned mode 0600." >&2; exit 1; }
done
exec 9> "$VM_ROOT/.deployment.lock"
flock -w 1200 9 || { echo 'Another deployment or rollback is still running.' >&2; exit 1; }
previous=''
if [[ -L "$VM_ROOT/current" ]]; then
  previous="$(basename "$(readlink -f "$VM_ROOT/current")")"
  [[ "$previous" =~ ^[0-9a-f]{40}$ && -d "$VM_ROOT/releases/$previous" ]] || { echo 'Current release pointer is invalid.' >&2; exit 1; }
  docker image inspect "volleyball-manager:$previous" >/dev/null
fi
candidate_env="$(mktemp "$VM_ROOT/config/.candidate-XXXXXX")"
printf 'APP_IMAGE=%s\n' "$image" > "$candidate_env"
saved_env="$(mktemp "$VM_ROOT/config/.saved-release-XXXXXX")"
cp -- "$VM_ROOT/config/release.env" "$saved_env"
compose() { docker compose --project-name volleyball-manager --env-file "$candidate_env" -f "$release/compose.yaml" "$@"; }
old_compose() { APP_IMAGE="volleyball-manager:$previous" docker compose --project-name volleyball-manager --env-file "$VM_ROOT/config/release.env" -f "$VM_ROOT/releases/$previous/compose.yaml" "$@"; }
wait_ready() {
  local attempt
  for attempt in $(seq 1 60); do
    local app_id
    app_id="$(docker ps -q --filter label=com.docker.compose.project=volleyball-manager --filter label=com.docker.compose.service=app)"
    if [[ -n "$app_id" && "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$app_id")" == healthy ]] && \
      curl --fail --silent --max-time 4 http://127.0.0.1:5050/api/v1/health/ready >/dev/null; then return 0; fi
    sleep 2
  done
  return 1
}
app_stopped=false
candidate_started=false
completed=false
migration_container=''
recover() {
  local status=$?
  trap - EXIT INT TERM
  # Ensure timed-out/interrupted owner work is gone before restarting web/worker.
  if [[ -n "$migration_container" ]] && docker container inspect "$migration_container" >/dev/null 2>&1; then
    docker stop -t 15 "$migration_container" >/dev/null 2>&1 || true
    if ! docker rm -f "$migration_container" >/dev/null 2>&1; then
      echo 'Migration cleanup failed; keep web/worker stopped and review owner work before restarting.' >&2
      rm -f -- "$candidate_env" "$saved_env"
      exit "$status"
    fi
  fi
  if [[ "$status" -ne 0 && "$app_stopped" == true && "$completed" != true ]]; then
    cp -- "$saved_env" "$VM_ROOT/config/release.env"
    if [[ -n "$previous" ]]; then
      ln -s "$VM_ROOT/releases/$previous" "$VM_ROOT/.restore-current-$$"
      mv -Tf "$VM_ROOT/.restore-current-$$" "$VM_ROOT/current"
      echo 'Release failed; restoring the previous application image. Database migrations are not reversed.' >&2
      if old_compose up -d --no-deps app && wait_ready; then
        echo "Previous application $previous is ready again." >&2
      else
        echo 'Previous application did not become ready. Leave database state intact and inspect private VM logs.' >&2
      fi
    elif [[ "$candidate_started" == true ]]; then
      compose stop -t 90 app >/dev/null 2>&1 || true
      echo 'First deployment failed; no earlier application image is available.' >&2
    fi
  fi
  rm -f -- "$candidate_env" "$saved_env"
  exit "$status"
}
trap recover EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ "$mode" == deploy ]]; then
  # Reuse a built immutable image on a repeat of the same commit.
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    timeout 1200 docker build --label "org.opencontainers.image.revision=$revision" --tag "$image" "$release"
  fi
  [[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" == "$revision" ]] || { echo 'Existing image has an unexpected revision label.' >&2; exit 1; }
else
  docker image inspect "$image" >/dev/null
  echo 'Rolling back application code only; the existing database schema and data remain unchanged.'
fi
# Existing PostgreSQL containers keep their original init mount and image.
postgres_id="$(docker ps -aq --filter label=com.docker.compose.project=volleyball-manager --filter label=com.docker.compose.service=postgres)"
if [[ -z "$postgres_id" ]]; then
  compose up -d postgres
  postgres_id="$(compose ps -q postgres)"
else
  [[ "$(printf '%s\n' "$postgres_id" | wc -l)" -eq 1 ]] || { echo 'Multiple PostgreSQL containers need operator review.' >&2; exit 1; }
  docker start "$postgres_id" >/dev/null
fi
postgres_ready=false
for attempt in $(seq 1 60); do
  if [[ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$postgres_id")" == healthy ]]; then postgres_ready=true; break; fi
  sleep 2
done
[[ "$postgres_ready" == true ]] || { echo 'PostgreSQL did not become healthy.' >&2; exit 1; }
if [[ "$mode" == deploy ]]; then
  migration_name="volleyball-manager-migration-$revision"
  [[ -z "$(docker ps -aq --filter label=volleyball-manager.migration=true)" && \
     -z "$(docker ps -aq --filter name="^/${migration_name}$")" ]] || { echo 'An earlier migration container requires operator review.' >&2; exit 1; }
fi
app_stopped=true
if [[ -n "$previous" ]]; then
  old_compose stop -t 90 app
else
  compose stop -t 90 app
fi
if [[ "$mode" == deploy ]]; then
  # Owner credentials exist only in this transient migration container.
  migration_container="$migration_name"
  timeout --kill-after=30s 300 docker compose --project-name volleyball-manager --env-file "$candidate_env" \
    -f "$release/compose.yaml" -f "$release/deploy/compose.operator.yaml" \
    run --rm --no-deps -T --interactive=false --name "$migration_container" \
    --label volleyball-manager.migration=true app alembic upgrade head
  migration_container=''
fi
candidate_started=true
compose up -d --no-deps app
wait_ready || { echo 'New application failed its bounded readiness check.' >&2; exit 1; }
# Commit the pointers only after the new service passes readiness.
if [[ -n "$previous" && "$previous" != "$revision" ]]; then
  printf '%s\n' "$previous" > "$VM_ROOT/config/.previous-release.tmp"
  mv -f "$VM_ROOT/config/.previous-release.tmp" "$VM_ROOT/config/previous-release"
fi
mv -f -- "$candidate_env" "$VM_ROOT/config/release.env"
ln -s "$release" "$VM_ROOT/.current-$$"
mv -Tf "$VM_ROOT/.current-$$" "$VM_ROOT/current"
completed=true
echo "Application release $revision passed container and loopback readiness. Verify https://volleyball.example.com separately."
