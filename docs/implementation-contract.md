# Implementation contract

Project root: VOLLEYBALL-MANAGER only. Stack: Python 3.14, Flask, SQLAlchemy 2, Alembic, PostgreSQL 18, modular vanilla JavaScript/CSS. Application package `volleyball`. Factory `volleyball.create_app`. All API paths `/api/v1`. PostgreSQL only; no SQLite. Demo identified everywhere, isolated to a local database.

## Shared Python interfaces

`volleyball.models` exports Base and ORM classes User, AuthSession, Token, Organization, Membership, Team, Venue, Court, Tournament, TournamentVenue, Division, Stage, Entry, RuleVersion, Match, CourtAssignment, Grant, ScorerSession, MatchEvent, ResultRevision, Audit, Outbox, Preference, Announcement and Job. Column definitions are in models.py.

`volleyball.db`: `get_db()` returns request-scoped SQLAlchemy Session; `set_scope(organization_id)` uses transaction-local PostgreSQL context. Request commits happen explicitly in mutation handlers; teardown rolls back/closes.

`volleyball.common`: `APIError(message, status=400, code='validation', details=None)`, `body()` JSON dict, `uuid_str()`, `now()`, `audit(action, entity_type, entity_id, before=None, after=None, reason=None)`, `emit(match, kind)` transactional outbox, `iso(datetime)`, `serialize(model)` safe general serializer (special match serializer in competition module).

`volleyball.auth`: blueprint `bp`, `init_auth(app)`, decorators `require_login`, `require_org(permission='read')`, helper `authorize_tournament(tournament)`. require_org reads X-Organization-ID header or organization_id query/body; validates active Membership, sets g.user/g.organization/g.membership and RLS scope. Roles admin (all), manager (manage,teams,schedule,results,read), scheduler (schedule,read), results (results,read), viewer (read). Membership.tournament_ids is optional JSON list limiting tournament scope. Platform admin alone has no customer membership privileges.

## API responses

Error: `{error:{code,message,details}, request_id}`. Common success dictionaries direct, list collections as `{items:[],total}`. All mutating cookie-auth requests require `X-CSRF-Token`, obtained from GET `/auth/me` response. Referee uses HttpOnly game-scoped cookie and same CSRF mechanism.

## Frontend contract

GET `/auth/me` -> `{user:{id,email,name,platform_admin,mfa_enabled}|null,organizations:[{id,name,slug,role,demo}],csrf_token}`. POST `/auth/login` {email,password,otp?}, `/auth/logout`; POST `/auth/reset/request` {email}, `/auth/reset/complete` {token,password}; `/auth/invitations/accept` {token,name,password}; GET/POST `/auth/profile`; GET `/auth/sessions`; DELETE `/auth/sessions/<id>`; POST `/auth/mfa/setup` and `/auth/mfa/confirm`.

Org header required below. GET `/dashboard` -> `{organization,tournaments,teams,venues,courts,matches,metrics,announcements,notifications}` with scoped filtered persisted data. GET/POST `/teams`, `/venues`, `/courts`, `/tournaments`; PATCH /resource/<id>. GET/POST `/memberships`, PATCH `/memberships/<id>`. GET/PATCH `/organizations/current`; GET/POST `/platform/organizations` restricted platform admin.

GET `/tournaments/<id>` -> `{tournament,divisions,stages,entries,matches,courts,venues,standings,announcements,display}`. POST `/tournaments/<id>/divisions` {name,rules?}; POST `/divisions/<id>/entries` {team_id,seed,pool?}; POST `/divisions/<id>/stages` {name,format,position,rules?,settings?}; POST `/stages/<id>/generate` {preview:true|false,start_at?,interval_minutes?,repeat?,pool_count?}. POST `/tournaments/<id>/validate`, `/publish` {visibility}, PATCH `/tournaments/<id>` for editable fields/lifecycle. GET/POST `/matches` ; GET/PATCH `/matches/<id>`. GET `/matches/<id>` -> match detail; POST `/matches/<id>/court` {court_id,reason?}; POST `/matches/<id>/release`; POST `/matches/<id>/qr` -> `{url,qr_svg,expires_at,display_seconds:10}` ONLY on reveal, never in match detail. POST `/matches/<id>/revoke`; POST `/matches/<id>/approve`; POST `/matches/<id>/correct` {reason,sets:[{a,b}],winner_id?,preview?}; POST `/matches/<id>/outcome` {outcome,reason}.

Match JSON: id,tournament_id,division_id,stage_id,team_a:{id,name,color},team_b:{...},court:{id,name}|null,planned_at,status,version,generation,rules,state:{sets:[{a,b,complete,winner}],set_index,serve:'a'|'b',timeouts:{a,b},clock_seconds,clock_anchor},result_status,outcome,winner_id,elapsed_seconds,referee_name. Null team displays TBD. Participant id is Entry id.

POST `/referee/exchange` {token,name?} -> {match,csrf_token,session:{generation}}; GET `/referee/state`; POST `/referee/resume` {takeover:true} -> {match,session:{generation}}; POST `/referee/action` {client_action_id,expected_version,generation,type,payload}. Types start,point {team:'a'|'b'},serve {team},timeout {team},pause,resume,next_set,undo,correct {a,b,reason},end. Returns `{match,receipt:{client_action_id,version},saved:true}`; 409 version conflict and 403 stale scorer generation. POST `/referee/renew`; POST `/referee/exit` revokes own session (doesn't end match).

GET `/public/<slug>` -> public-safe tournament detail; GET `/public/<slug>/live` -> compact current match/court projections with metadata configuration digest and standings generation. GET `/public/<slug>/events` returns a bounded JSON outbox poll, rechecking visibility. Public and display clients poll compact state each second after initial full detail; organizer overview/schedule polling is every two seconds. Polls are single-flight and refresh authorized central state after reconnection. `/t/<slug>` is the public page and `/display/<slug>` the dedicated display; `/referee#<secret>` exchange removes the fragment immediately; `/` is the organizer app; `/login` the identity experience. GET `/tournaments/<id>/weather` and `/public/<slug>/weather` return real/unavailable adapter output.

GET `/reports?type=...&tournament_id=...&division_id=...&team_id=...&status=...&from=...&to=...` -> `{columns,rows,total,metadata}`. GET `/reports/export?format=csv|xlsx|pdf&...` -> authorized filtered download. POST `/exports` creates persisted job {type,format,filters}, GET `/exports`/`/exports/<id>`, GET `/exports/<id>/download`. POST `/imports/preview` multipart CSV + type; POST `/imports/<id>/commit`. GET `/imports/template?type=teams|entries|fixtures|referees`. GET `/audit`. GET/POST `/preferences` {kind:'organizer'|'report',layout}; GET/PATCH `/tournaments/<id>/display` separate public configuration; GET `/organizations/export` business-data JSON. POST `/tournaments/<id>/announcements`. GET `/health/live`, `/health/ready`; GET `/operations` platform admin.

## Division of work

Root owns models, app factory/db/common, competition services/routes, scorer engine, court transactions, migrations, local env, scoring/race tests.
Identity/admin agent owns auth.py, admin.py, seed.py and identity tests (do not edit root-owned files).
Frontend agent owns templates/* static/* and interface routes/ui.py (do not edit backend).
Reports/operations agent owns reports.py, weather.py, operations.py, docs (except this contract), scripts backup/runbook helpers and reporting tests.

All work must be functional, durable, safe by scope, and accurately document remaining production/device validation. Do not claim external integrations configured or production readiness.
