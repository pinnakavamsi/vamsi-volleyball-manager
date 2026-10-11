"""Relational identities and organization-scoped PostgreSQL application data."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey,
    ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, Float)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, declared_attr
from flask_login import UserMixin


def uid(): return str(uuid.uuid4())
def utcnow(): return datetime.now(timezone.utc)
class Base(DeclarativeBase): pass
class Record:
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
class Owned(Record):
    organization_id = Column(UUID(as_uuid=False), ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False, index=True)
    @declared_attr.directive
    def __table_args__(cls):
        return (UniqueConstraint('id','organization_id'),)

class User(UserMixin, Record, Base):
    __tablename__ = 'users'
    email = Column(String(254), unique=True, nullable=False)
    name = Column(String(120), nullable=False)
    password_hash = Column(Text, nullable=False)
    password_change_required = Column(Boolean, default=False, server_default='false', nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    platform_admin = Column(Boolean, default=False, nullable=False)
    mfa_secret = Column(Text)
    mfa_enabled = Column(Boolean, default=False, nullable=False)
    mfa_recovery_hashes = Column(JSONB, default=list, nullable=False)
    mfa_last_counter = Column(Integer, default=-1, nullable=False)
    failed_login_attempts = Column(Integer, default=0, nullable=False)
    locked_until = Column(DateTime(timezone=True))
    @property
    def is_active(self): return self.active
class PlatformSetup(Base):
    """Permanent completion marker; administrator removal never reopens setup."""
    __tablename__ = 'platform_setup'
    id = Column(Integer, primary_key=True)
    completed_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (CheckConstraint('id = 1', name='ck_platform_setup_singleton'),)
class AuthSession(Record, Base):
    __tablename__ = 'auth_sessions'
    user_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False)
    csrf_token = Column(String(128))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True))
    last_seen_at = Column(DateTime(timezone=True), default=utcnow)
    user_agent = Column(String(250))
    mfa_verified = Column(Boolean, default=False)
class Token(Record, Base):
    __tablename__ = 'identity_tokens'
    kind = Column(String(30), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    email = Column(String(254))
    user_id = Column(UUID(as_uuid=False), ForeignKey('users.id'))
    organization_id = Column(UUID(as_uuid=False), ForeignKey('organizations.id'))
    data = Column(JSONB, default=dict, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True))
    revoked_at = Column(DateTime(timezone=True))
class Organization(Record, Base):
    __tablename__ = 'organizations'
    name = Column(String(160), nullable=False)
    slug = Column(String(120), unique=True, nullable=False)
    timezone = Column(String(64), default='America/New_York', nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    demo = Column(Boolean, default=False, nullable=False)
    logo = Column(Text)
    color = Column(String(20), default='#11b981')
    contact = Column(JSONB, default=dict, nullable=False)
    settings = Column(JSONB, default=dict, nullable=False)
class Membership(Record, Base):
    __tablename__ = 'memberships'
    organization_id = Column(UUID(as_uuid=False), ForeignKey('organizations.id'), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False, index=True)
    role = Column(String(30), default='viewer', nullable=False)
    status = Column(String(20), default='active', nullable=False)
    tournament_ids = Column(JSONB, default=list, nullable=False)
    invited_at = Column(DateTime(timezone=True))
    activated_at = Column(DateTime(timezone=True), default=utcnow)
    suspended_at = Column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint('organization_id','user_id'), CheckConstraint("role IN ('admin','manager','scheduler','results','viewer')"),)
class Team(Owned, Base):
    __tablename__ = 'teams'
    name = Column(String(160), nullable=False)
    abbreviation = Column(String(20))
    color = Column(String(20), default='#11b981')
    logo = Column(Text)
    contact = Column(JSONB, default=dict, nullable=False)
    archived = Column(Boolean, default=False, nullable=False)
class Venue(Owned, Base):
    __tablename__ = 'venues'
    name = Column(String(160), nullable=False)
    address = Column(Text)
    latitude = Column(Float)
    longitude = Column(Float)
    timezone = Column(String(64), default='America/New_York', nullable=False)
    indoor = Column(Boolean, default=True, nullable=False)
    settings = Column(JSONB, default=dict, nullable=False)
class Court(Owned, Base):
    __tablename__ = 'courts'
    venue_id = Column(UUID(as_uuid=False), nullable=False)
    name = Column(String(120), nullable=False)
    available = Column(Boolean, default=True, nullable=False)
    settings = Column(JSONB, default=dict, nullable=False)
    __table_args__ = (UniqueConstraint('id','organization_id'),ForeignKeyConstraint(['venue_id','organization_id'], ['venues.id','venues.organization_id']),)
class Tournament(Owned, Base):
    __tablename__ = 'tournaments'
    name = Column(String(180), nullable=False)
    slug = Column(String(140), unique=True, nullable=False)
    description = Column(Text, default='')
    competition_type = Column(String(30), default='tournament', nullable=False)
    status = Column(String(25), default='draft', nullable=False)
    visibility = Column(String(15), default='private', nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    timezone = Column(String(64), default='America/New_York', nullable=False)
    rules = Column(JSONB, default=dict, nullable=False)
    settings = Column(JSONB, default=dict, nullable=False)
    display = Column(JSONB, default=dict, nullable=False)
    standings_generation = Column(Integer, default=0, nullable=False)
    __table_args__ = (UniqueConstraint('id','organization_id'),CheckConstraint('end_date >= start_date'),CheckConstraint("visibility IN ('private','public')"),)
class Division(Owned, Base):
    __tablename__ = 'divisions'
    tournament_id = Column(UUID(as_uuid=False), nullable=False)
    name = Column(String(120), nullable=False)
    category = Column(String(80))
    rules = Column(JSONB, default=dict, nullable=False)
    settings = Column(JSONB, default=dict, nullable=False)
    __table_args__ = (UniqueConstraint('id','organization_id'),UniqueConstraint('id','tournament_id','organization_id'),ForeignKeyConstraint(['tournament_id','organization_id'], ['tournaments.id','tournaments.organization_id']),)
class Stage(Owned, Base):
    __tablename__ = 'stages'
    tournament_id = Column(UUID(as_uuid=False), nullable=False)
    division_id = Column(UUID(as_uuid=False), nullable=False)
    name = Column(String(120), nullable=False)
    format = Column(String(30), default='round_robin', nullable=False)
    position = Column(Integer, default=1, nullable=False)
    rules = Column(JSONB, default=dict, nullable=False)
    settings = Column(JSONB, default=dict, nullable=False)
    __table_args__ = (UniqueConstraint('id','organization_id'),UniqueConstraint('id','division_id','tournament_id','organization_id'),ForeignKeyConstraint(['division_id','tournament_id','organization_id'], ['divisions.id','divisions.tournament_id','divisions.organization_id']),)
class Entry(Owned, Base):
    __tablename__ = 'entries'
    tournament_id = Column(UUID(as_uuid=False), nullable=False)
    division_id = Column(UUID(as_uuid=False), nullable=False)
    team_id = Column(UUID(as_uuid=False), nullable=False)
    name = Column(String(160), nullable=False)
    color = Column(String(20), default='#11b981')
    seed = Column(Integer, default=1, nullable=False)
    pool = Column(String(40))
    status = Column(String(20), default='active', nullable=False)
    roster = Column(JSONB, default=list, nullable=False)
    __table_args__ = (UniqueConstraint('id','organization_id'),UniqueConstraint('id','division_id','tournament_id','organization_id'),UniqueConstraint('division_id','team_id'),ForeignKeyConstraint(['division_id','tournament_id','organization_id'], ['divisions.id','divisions.tournament_id','divisions.organization_id']),ForeignKeyConstraint(['team_id','organization_id'],['teams.id','teams.organization_id']),)
class Match(Owned, Base):
    __tablename__ = 'matches'
    tournament_id = Column(UUID(as_uuid=False), nullable=False)
    division_id = Column(UUID(as_uuid=False), nullable=False)
    stage_id = Column(UUID(as_uuid=False), nullable=False)
    entry_a_id = Column(UUID(as_uuid=False))
    entry_b_id = Column(UUID(as_uuid=False))
    source_a_id = Column(UUID(as_uuid=False), ForeignKey('matches.id'))
    source_b_id = Column(UUID(as_uuid=False), ForeignKey('matches.id'))
    round_number = Column(Integer, default=1)
    pool = Column(String(40))
    planned_at = Column(DateTime(timezone=True))
    planned_duration = Column(Integer, default=60, nullable=False)
    actual_start = Column(DateTime(timezone=True))
    actual_end = Column(DateTime(timezone=True))
    status = Column(String(25), default='scheduled', nullable=False)
    version = Column(Integer, default=0, nullable=False)
    generation = Column(Integer, default=0, nullable=False)
    rules = Column(JSONB, default=dict, nullable=False)
    rule_snapshot = Column(JSONB)
    state = Column(JSONB, default=dict, nullable=False)
    winner_id = Column(UUID(as_uuid=False), ForeignKey('entries.id'))
    outcome = Column(String(25))
    result_status = Column(String(25), default='unsubmitted', nullable=False)
    result_revision = Column(Integer, default=0, nullable=False)
    referee_name = Column(String(120))
    settings = Column(JSONB, default=dict, nullable=False)
    __table_args__ = (UniqueConstraint('id','organization_id'),CheckConstraint('version >= 0 AND generation >= 0'),CheckConstraint('entry_a_id IS NULL OR entry_b_id IS NULL OR entry_a_id <> entry_b_id'),ForeignKeyConstraint(['stage_id','division_id','tournament_id','organization_id'], ['stages.id','stages.division_id','stages.tournament_id','stages.organization_id']),ForeignKeyConstraint(['entry_a_id','division_id','tournament_id','organization_id'],['entries.id','entries.division_id','entries.tournament_id','entries.organization_id']),ForeignKeyConstraint(['entry_b_id','division_id','tournament_id','organization_id'],['entries.id','entries.division_id','entries.tournament_id','entries.organization_id']),Index('ix_match_scope_time','organization_id','tournament_id','planned_at'),Index('ix_match_scope_status','organization_id','status'))
class CourtAssignment(Owned, Base):
    __tablename__ = 'court_assignments'
    match_id = Column(UUID(as_uuid=False), nullable=False)
    court_id = Column(UUID(as_uuid=False), nullable=False)
    status = Column(String(20), default='reserved', nullable=False)
    assigned_by = Column(UUID(as_uuid=False), ForeignKey('users.id'))
    expires_at = Column(DateTime(timezone=True))
    started_at = Column(DateTime(timezone=True))
    released_at = Column(DateTime(timezone=True))
    reason = Column(Text)
    __table_args__ = (UniqueConstraint('id','organization_id'),ForeignKeyConstraint(['match_id','organization_id'], ['matches.id','matches.organization_id']),ForeignKeyConstraint(['court_id','organization_id'], ['courts.id','courts.organization_id']),Index('uq_active_court','court_id',unique=True,postgresql_where=released_at.is_(None)),Index('uq_active_match_court','match_id',unique=True,postgresql_where=released_at.is_(None)),)
class Grant(Owned, Base):
    __tablename__ = 'qr_grants'
    match_id = Column(UUID(as_uuid=False), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    issued_by = Column(UUID(as_uuid=False), ForeignKey('users.id'))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint('id','organization_id'),ForeignKeyConstraint(['match_id','organization_id'], ['matches.id','matches.organization_id']),)
class ScorerSession(Owned, Base):
    __tablename__ = 'scorer_sessions'
    match_id = Column(UUID(as_uuid=False), nullable=False)
    grant_id = Column(UUID(as_uuid=False), ForeignKey('qr_grants.id'))
    token_hash = Column(String(64), unique=True, nullable=False)
    generation = Column(Integer, nullable=False)
    name = Column(String(120))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True))
    last_seen_at = Column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint('id','organization_id'),ForeignKeyConstraint(['match_id','organization_id'], ['matches.id','matches.organization_id']),Index('uq_active_scorer','match_id',unique=True,postgresql_where=revoked_at.is_(None) & (generation >= 0)),)
class MatchEvent(Owned, Base):
    __tablename__ = 'match_events'
    match_id = Column(UUID(as_uuid=False), nullable=False)
    sequence = Column(Integer, nullable=False)
    client_action_id = Column(UUID(as_uuid=False), nullable=False)
    type = Column(String(30), nullable=False)
    actor_id = Column(UUID(as_uuid=False), ForeignKey('users.id'))
    session_id = Column(UUID(as_uuid=False), ForeignKey('scorer_sessions.id'))
    payload = Column(JSONB, default=dict, nullable=False)
    expected_version = Column(Integer, nullable=False)
    new_version = Column(Integer, nullable=False)
    receipt = Column(JSONB, default=dict, nullable=False)
    __table_args__ = (UniqueConstraint('id','organization_id'),UniqueConstraint('match_id','sequence'),UniqueConstraint('match_id','client_action_id'),ForeignKeyConstraint(['match_id','organization_id'], ['matches.id','matches.organization_id']),)
class ResultRevision(Owned, Base):
    __tablename__ = 'result_revisions'
    match_id = Column(UUID(as_uuid=False), nullable=False)
    revision = Column(Integer, nullable=False)
    sets = Column(JSONB, default=list, nullable=False)
    winner_id = Column(UUID(as_uuid=False), ForeignKey('entries.id'))
    outcome = Column(String(25), default='normal')
    status = Column(String(25), default='submitted')
    approved_by = Column(UUID(as_uuid=False), ForeignKey('users.id'))
    approved_at = Column(DateTime(timezone=True))
    reason = Column(Text)
    predecessor_id = Column(UUID(as_uuid=False), ForeignKey('result_revisions.id'))
    __table_args__ = (UniqueConstraint('id','organization_id'),UniqueConstraint('match_id','revision'),ForeignKeyConstraint(['match_id','organization_id'], ['matches.id','matches.organization_id']),)
class Audit(Owned, Base):
    __tablename__ = 'audit_events'
    organization_id = Column(UUID(as_uuid=False), ForeignKey('organizations.id'), nullable=True, index=True)
    actor_id = Column(UUID(as_uuid=False), ForeignKey('users.id'))
    session_id = Column(UUID(as_uuid=False), ForeignKey('scorer_sessions.id'))
    actor_type = Column(String(30), nullable=False)
    action = Column(String(80), nullable=False)
    entity_type = Column(String(40), nullable=False)
    entity_id = Column(UUID(as_uuid=False))
    before = Column(JSONB)
    after = Column(JSONB)
    reason = Column(Text)
    __table_args__ = (UniqueConstraint('id','organization_id'),Index('ix_audit_scope_time','organization_id','created_at'),)
class SupportCase(Owned, Base):
    __tablename__ = 'support_cases'
    opened_by = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False)
    subject = Column(String(180), nullable=False)
    category = Column(String(40), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String(30), default='open', server_default='open', nullable=False)
    __table_args__ = (UniqueConstraint('id','organization_id'), CheckConstraint("status IN ('open','in_progress','waiting_on_organization','resolved','closed')"), Index('ix_support_case_scope_status_time','organization_id','status','created_at'))
class SupportMessage(Owned, Base):
    __tablename__ = 'support_messages'
    case_id = Column(UUID(as_uuid=False), nullable=False)
    author_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False)
    author_type = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    __table_args__ = (UniqueConstraint('id','organization_id'), ForeignKeyConstraint(['case_id','organization_id'], ['support_cases.id','support_cases.organization_id'], ondelete='RESTRICT'), CheckConstraint("author_type IN ('organization','platform_admin')"), Index('ix_support_message_case_time','case_id','created_at'))
class Outbox(Owned, Base):
    __tablename__ = 'outbox_events'
    match_id = Column(UUID(as_uuid=False), ForeignKey('matches.id'))
    tournament_id = Column(UUID(as_uuid=False), ForeignKey('tournaments.id'))
    kind = Column(String(40), nullable=False)
    version = Column(Integer, nullable=False)
    payload = Column(JSONB, default=dict, nullable=False)
    published_at = Column(DateTime(timezone=True))
    attempts = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint('id','organization_id'),Index('ix_outbox_pending','organization_id','created_at',postgresql_where=published_at.is_(None)),)
class Preference(Owned, Base):
    __tablename__ = 'preferences'
    user_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False)
    kind = Column(String(30), nullable=False)
    name = Column(String(120), default='Default')
    layout = Column(JSONB, default=dict, nullable=False)
    __table_args__ = (UniqueConstraint('id','organization_id'),UniqueConstraint('organization_id','user_id','kind','name'),)
class Announcement(Owned, Base):
    __tablename__ = 'announcements'
    tournament_id = Column(UUID(as_uuid=False), ForeignKey('tournaments.id'))
    title = Column(String(180), nullable=False)
    content = Column(Text, nullable=False)
    visibility = Column(String(15), default='public', nullable=False)
    publish_at = Column(DateTime(timezone=True), default=utcnow)
    expires_at = Column(DateTime(timezone=True))
class Job(Owned, Base):
    __tablename__ = 'jobs'
    user_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False)
    kind = Column(String(30), nullable=False)
    status = Column(String(20), default='queued', nullable=False)
    type = Column(String(40))
    format = Column(String(12))
    filters = Column(JSONB, default=dict, nullable=False)
    data = Column(JSONB, default=dict, nullable=False)
    progress = Column(Integer, default=0, nullable=False)
    file_path = Column(Text)
    error = Column(Text)
    expires_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
class WeatherCache(Owned, Base):
    __tablename__ = 'weather_cache'
    venue_id = Column(UUID(as_uuid=False), ForeignKey('venues.id'), nullable=False)
    provider = Column(String(80), nullable=False)
    payload = Column(JSONB, default=dict, nullable=False)
    fetched_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint('id','organization_id'),UniqueConstraint('venue_id','provider'),)

# Hash discovery only is allowed for delegated credentials; all actual match data uses RLS.
RLS_TABLES = [c.__tablename__ for c in (Team,Venue,Court,Tournament,Division,Stage,Entry,Match,CourtAssignment,MatchEvent,ResultRevision,Audit,SupportCase,SupportMessage,Outbox,Preference,Announcement,Job,WeatherCache)]

class RuleVersion(Owned, Base):
    __tablename__='rule_versions'
    tournament_id=Column(UUID(as_uuid=False),ForeignKey('tournaments.id'),nullable=False)
    division_id=Column(UUID(as_uuid=False),ForeignKey('divisions.id'))
    stage_id=Column(UUID(as_uuid=False),ForeignKey('stages.id'))
    scope_type=Column(String(20),nullable=False)
    scope_id=Column(UUID(as_uuid=False),nullable=False)
    version=Column(Integer,nullable=False)
    rules=Column(JSONB,nullable=False)
    reason=Column(Text)
    created_by=Column(UUID(as_uuid=False),ForeignKey('users.id'))
    __table_args__=(UniqueConstraint('id','organization_id'),UniqueConstraint('scope_type','scope_id','version'),)
class TournamentVenue(Owned,Base):
    __tablename__='tournament_venues'
    tournament_id=Column(UUID(as_uuid=False),nullable=False)
    venue_id=Column(UUID(as_uuid=False),nullable=False)
    active=Column(Boolean,default=True,nullable=False)
    __table_args__=(UniqueConstraint('id','organization_id'),UniqueConstraint('tournament_id','venue_id'),ForeignKeyConstraint(['tournament_id','organization_id'],['tournaments.id','tournaments.organization_id']),ForeignKeyConstraint(['venue_id','organization_id'],['venues.id','venues.organization_id']),)
RLS_TABLES+=['rule_versions','tournament_venues']
