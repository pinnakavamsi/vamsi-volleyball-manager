# Versioned API

Base path: `/api/v1`. JSON responses use direct dictionaries or `{items,total}`; report tables use `{columns,rows,total,metadata,page,page_size}`. Errors are `{error:{code,message,details},request_id}` with an appropriate HTTP status. Private cookie-authenticated mutations send `X-CSRF-Token` from `/auth/me` and `X-Organization-ID` for organization routes. Referee mutations use the delegated cookie and CSRF token returned by exchange. Never include a raw grant in a query string or log.

## Identity and administration

`GET /auth/me` includes `setup_required`. For an empty installation, `POST /auth/setup` with `{email,name,password}` creates only the first platform administrator and returns an authenticated user/session with a new CSRF token. Obtain the signed-session CSRF token from `/auth/me` and send a matching Origin. Setup is throttled, serialized in PostgreSQL, and permanently closes after the first administrator; repeated setup returns 409 `setup_complete`. Browser `/` and `/login` lead to `/setup` while required. No account defaults or organization/tournament fixture is created.

`GET /auth/me`; `POST /auth/login` with email/password/optional OTP; `POST /auth/logout`; password-reset request/complete; invitation acceptance; profile GET/POST; session list/revocation; MFA setup/confirmation. Organizations/current is GET/PATCH. Teams, venues and courts support lists/creation and PATCH by ID. Organization membership list/invitation/update uses administrator permission. Platform organizations/provisioning and operations require the separate platform administrator scope. A platform binding does not imply customer membership.

Organization headers follow a selected active membership. Roles are admin, manager, scheduler, results and viewer; assignments may further restrict tournament IDs. Backend permissions apply to requests and jobs. Lists are bounded; a guessed cross-organization UUID cannot establish scope.

`GET /audit` provides the selected organization's authorized activity log. `GET/POST /support-cases` lists organization tickets or opens one with `{subject,category,description}`. Organization staff can reply with `POST /support-cases/<id>/messages` and `{message}`. Platform administrators use `GET /platform/support-cases`, reply through `/platform/support-cases/<id>/messages`, and update status with `PATCH /platform/support-cases/<id>` and `{status}`. Platform access to ticket correspondence and all ticket changes are recorded in the affected organization's audit log. Support case and message policies permit either the selected tenant or the explicit platform support scope; platform support does not grant access to tournament records.

## Competitions and games

Tournaments GET/POST/PATCH, tournament detail, divisions, entries, stages, fixture-generation preview/commit, setup validation and publication are supported. Publication lifecycle and public/private visibility are independent. The organizer game endpoints provide match detail, schedule update, court assignment/release, private QR reveal/revocation, approval/correction and administrative outcome. A QR appears only in the authorized reveal response and never in normal match detail or exports.

The authoritative match representation includes tournament/division/stage, entry identities, court or null, planned/actual times, lifecycle, version, scorer generation, effective rules/rule snapshot, set state, serve, timeouts, timer state and result status. Null court means Court to be assigned. Read-only public representations omit private contact, audit, QR and session data.

Historical ranking policy changes require a reviewed preview and explicit recalculation. POST `/rules/preview` with `{scope_type:"tournament"|"division"|"stage",scope_id,rules}` returns before/after authoritative tables and the current source generation. Apply the reviewed rules through the corresponding resource PATCH with `recalculate_history:true` and a reason when finalized history is affected. Each accepted policy change records a relational rule version; started match scoring snapshots remain frozen.

Fixture generation uses POST `/stages/<id>/generate`; `preview` defaults to true, and only an explicit `preview:false` commits the reviewed structure. League and round-robin stages accept repeated encounters. A later `single_elimination` stage can use `settings.source_stage_id` pointing to an earlier league/round-robin stage in the same division and `qualifier_count` for season playoffs. Pool-to-knockout generation creates its playoff stage and qualifier placeholders together; `qualify_per_pool` and optional `wildcard_places` define the reviewed places. Source results must be finalized before advancement. Wildcards use the configured cross-pool policy and unresolved ties block qualification. POST `/divisions/<id>/resolve-tie` records a reviewed unique entry order plus reason; started downstream games retain their played teams and require adjudication when a corrected source would change them. The supported qualification links do not implement arbitrary user-defined bracket formulas.

An ended knockout without a winner can be adjudicated through POST `/matches/<id>/resolve-winner` with `{winner_id,reason}`. Choose an entry that actually played this game. A new result revision preserves the existing sets and prior revision; it does not fabricate rallies. Approval requires a knockout winner. Finalized changes use the guarded progression path and flag any already-started downstream conflict. Division standings and explicit audited tie resolution use GET `/divisions/<id>/standings` and POST `/divisions/<id>/resolve-tie`.

## Delegated scoring examples

```json
{"token":"fragment-secret-exchanged-once","name":"Optional unverified referee name"}
```

POST this to `/referee/exchange`; the UI removes the browser fragment first. Store no raw secret in a public link. The response establishes an HttpOnly delegated cookie and supplies CSRF token, exact match and generation. `GET /referee/state` recovers persisted state without changing court occupancy.

```json
{
  "client_action_id":"9b6afc57-0983-456d-adfd-1f2403ecf7a2",
  "expected_version":12,
  "generation":2,
  "type":"point",
  "payload":{"team":"a"}
}
```

POST to `/referee/action`. An accepted action returns `{match,receipt:{client_action_id,version},saved:true}` after the PostgreSQL transaction commits. Retry the same ID after a lost response; authorized deduplication returns its receipt. Types: start, point, serve, timeout, pause, resume, next_set, undo, correct and end. Correct requires a reason. End is explicit; set completion alone never releases a court.

```json
{"takeover":true}
```

POST to `/referee/resume` after the user confirms taking control. The response includes the new generation; the old writer is fenced. A stale expected version yields HTTP 409 with current state/recovery guidance. A superseded scorer session/generation yields 403. Neither means the unsent point was saved. Offline actions stay Pending until accepted; a different device cannot recover another device's unsent queue.

## Public and presentation

`GET /public/<slug>` is the initial public-safe overview. Subsequent `/public/<slug>/live` polls return only bounded current match projections/participants/court information and current court statuses, with `configuration_version`, `standings_generation` and `updated_at`. They do not reread rankings or event history, or return rule payloads. Merge matches by ID with the initial metadata; refresh the full overview when configuration/standings generation or the match-ID set changes. More than 1,000 matches returns `full_refresh:true` without a misleading truncated ID set. `/public/<slug>/events` separately exposes bounded durable change notices. Every endpoint resolves publication/visibility again, and the UI clears content when anonymous access is revoked. `/t/<slug>` is the spectator page and `/display/<slug>` the distinct presentation. Public display configuration is read as safe data; authorized organizers edit `/tournaments/<id>/display` with a separate layout from user preferences. Weather endpoints are `/tournaments/<id>/weather` and `/public/<slug>/weather`, with `units=celsius|fahrenheit`.

Display `widgets` and `sizes` support `live`, `standings`, `upcoming`, `results`, `brackets`, `announcements`, `weather` and `sponsors`; sizes are `normal` or `wide`. Sponsor images use validated http/https URLs or local image paths; sponsor website links require HTTPS without embedded credentials. Active content and unsafe URL schemes are rejected before saving.

`configuration_version` is an opaque digest shared by overview/live responses. A single aggregate metadata query covers organization branding, divisions/stages/entries, associated venue/court configuration, stable match rule/snapshot inputs and currently visible public announcements. Scheduled announcement publication/expiry changes the signature at the current database time even without a new write. Rally score/version/state/time updates do not invalidate it. Do not interpret the digest as an ISO timestamp or a result generation.

## Reports, imports and worker jobs

`GET /reports` accepts `type` and filters documented in `reporting.md`. `GET /reports/export?format=csv|xlsx|pdf` returns the same authorized filtered persisted rows. Direct exports are bounded to 2,000 output rows; larger exports use:

```json
{"type":"matches","format":"xlsx","filters":{"tournament_id":"YOUR-UUID","result_status":"finalized"}}
```

POST `/exports` returns a 202 queued job. `GET /exports`, `/exports/<id>`, DELETE `/exports/<id>` and GET `/exports/<id>/download` support state, safe cancellation and an authorized expiring download. Worker `flask --app volleyball worker --once` processes a batch. Status is queued/running/completed/failed/cancelled/expired, progress 0–100. Download is not a permanent share URL. Worker and download recheck requester access; changing tournament assignment can invalidate an already generated broader file.

Public downloads use `/public/<slug>/reports/export` for explicitly enabled public-safe types in `display.public_reports`. Tournament privacy is checked again before the generated public response. `GET /imports/template?type=teams|entries|fixtures|referees` produces exact CSV headers. POST multipart `/imports/preview` with `type` and CSV `file`; preview returns private row feedback/job ID. `GET /imports/<id>` returns feedback. POST `/imports/<id>/commit` reruns validation and commits all rows together, or returns 422 row errors without changing business data. Import previews expire after 24 hours.

`GET /audit` returns authorized actor/time/reason/change summaries; it omits raw payloads and never grants a scoped manager access to unrelated organization audit rows. Preferences GET/POST stores per-user/per-organization organizer or report layouts. Administrator-only `GET /organizations/export` returns a documented business JSON package without credential or backup material.

Operator CLI `verify-projections --organization-id UUID` compares current match state to immutable accepted journal snapshots. `rebuild-match --organization-id UUID --match-id UUID --confirm` applies a reviewed projection rebuild with audit history. These are operator recovery tools rather than anonymous or organizer self-service rollback endpoints.

## Operations

`GET /health/live` is lightweight process liveness. `GET /health/ready` checks PostgreSQL and migrated schema and returns 503 when unavailable. `GET /operations` exposes private platform aggregate backlog/heartbeat/storage/pool/backup snapshots. External provider and email failures return accurate unavailable/queued states and do not change an acknowledged score.
