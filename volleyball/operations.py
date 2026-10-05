"""Lightweight public probes and private platform operational snapshots."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import time
from flask import Blueprint, current_app
from sqlalchemy import select, func, text
from .auth import require_platform_admin
from .common import iso, now
from .db import get_db, set_scope
from .models import Organization, Job, Outbox, ScorerSession, CourtAssignment, RLS_TABLES

bp=Blueprint('operations',__name__,url_prefix='/api/v1')


@bp.get('/health/live')
def liveness(): return {'status':'alive','service':'volleyball-manager'}


@bp.get('/health/ready')
def readiness():
    connected=False
    try:
        db=get_db();db.execute(text('SELECT 1'))
        connected=True
        privileges=db.execute(text("""SELECT r.rolsuper, r.rolbypassrls,
            EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname<>'alembic_version'
                AND pg_has_role(r.oid,c.relowner,'MEMBER')) AS inherits_table_ownership
            FROM pg_roles r WHERE r.rolname=current_user""")).one()
        if any(privileges):return {'status':'not_ready','database':'available','runtime_role':'unsafe'},503
        tables=db.execute(text("SELECT to_regclass('public.match_events'), to_regclass('public.jobs'), to_regclass('public.alembic_version')")).one()
        if any(value is None for value in tables): return {'status':'not_ready','database':'available','schema':'migration_required'},503
        version=db.scalar(text('SELECT version_num FROM alembic_version LIMIT 1'))
        expected=current_app.config.get('SCHEMA_REVISION')
        if not expected:
            from alembic.config import Config
            from alembic.script import ScriptDirectory
            configuration=Config();configuration.set_main_option('script_location',str(Path(current_app.root_path).parent/'migrations'))
            expected=ScriptDirectory.from_config(configuration).get_current_head()
        if not version or not expected or expected!=version: return {'status':'not_ready','database':'available','schema':'migration_required'},503
        total,secured=db.execute(text("""SELECT count(*), count(*) FILTER (WHERE c.relrowsecurity)
             FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname=ANY(:tables)"""),{'tables':RLS_TABLES}).one()
        if total!=len(RLS_TABLES) or secured!=len(RLS_TABLES):return {'status':'not_ready','database':'available','schema':'row_security_required'},503
        return {'status':'ready','database':'available','schema':'available'}
    except Exception:
        get_db().rollback()
        return {'status':'not_ready','database':'available' if connected else 'unavailable',
                'schema':'validation_failed' if connected else 'unavailable'},503


def backup_status():
    configured=current_app.config.get('BACKUP_STATUS_PATH')
    if not configured: return {'status':'not_configured','message':'Configure encrypted independent storage and backup monitoring before production launch.'}
    try:
        payload=json.loads(Path(configured).read_text())
        stamp=_status_time(payload['last_success_at'])
        age=max(0,(now()-stamp).total_seconds())
        wal={'status':'not_configured'}
        archive_mode=get_db().scalar(text('SHOW archive_mode'))
        if payload.get('pitr_enabled'):
            try:
                value=json.loads(Path(configured).with_name('wal-status.json').read_text())
                archived=_status_time(value['archived_at'])
                lag=max(0,(now()-archived).total_seconds())
                wal={'status':'stale' if lag>120 else 'recorded_success','last_archived_at':archived.isoformat(),'age_seconds':int(lag)}
            except (OSError,ValueError,KeyError,TypeError):wal={'status':'unavailable','message':'No readable continuous archive status; investigate archiver failures.'}
        return {'status':'stale' if age>86400 else 'recorded_success','last_success_at':stamp.isoformat(),'age_seconds':int(age),
                'type':payload.get('type'),'continuous_archive_configured_at_backup':bool(payload.get('pitr_enabled',False)),
                'current_archive_mode':archive_mode,'pitr_enabled':bool(payload.get('pitr_enabled',False) and archive_mode in {'on','always'} and wal['status']=='recorded_success'),
                'wal_archive':wal,
                'message':'Local status records a completed package; independent storage and restore validation require operator confirmation.'}
    except (OSError,ValueError,KeyError,TypeError): return {'status':'unavailable','message':'Backup status could not be read; investigate the backup operator.'}


def _status_time(value):
    if not isinstance(value,str):
        raise ValueError('Backup timestamps must be ISO strings.')
    stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
    if stamp.tzinfo is None or stamp>now()+timedelta(minutes=5):
        raise ValueError('Backup timestamps must include a timezone and cannot be in the future.')
    return stamp


def performance_snapshot():
    """Read one database sample and its real cumulative counters, with no retained series."""
    db=get_db()
    started=time.perf_counter()
    db.execute(text('SELECT 1')).scalar_one()
    round_trip_ms=round((time.perf_counter()-started)*1000,3)
    stats=db.execute(text("""SELECT datname,numbackends,xact_commit,xact_rollback,deadlocks,
        stats_reset,pg_database_size(current_database()) AS size_bytes
        FROM pg_stat_database WHERE datname=current_database()""")).mappings().one()
    return {'sampled_at':iso(now()),'database_round_trip_ms':round_trip_ms,
        'measurement':'One SELECT 1 round trip observed by this request. Database counters are cumulative, not request rates.',
        'database':{'name':stats['datname'],'connections':stats['numbackends'],
            'connections_definition':'All backends connected to the current database, including idle connections.',
            'transactions_committed':stats['xact_commit'],'transactions_rolled_back':stats['xact_rollback'],
            'deadlocks':stats['deadlocks'],'size_bytes':stats['size_bytes'],
            'stats_reset_at':iso(stats['stats_reset']),'counters':'cumulative_since_reset'}}


def service_snapshot():
    result=readiness()
    payload=result[0] if isinstance(result,tuple) else result
    return {**payload,'application':'available',
        'checks':'This HTTP request, database connectivity, schema revision, and runtime database role isolation.'}


@bp.get('/operations')
@require_platform_admin
def operations():
    db=get_db();orgs=list(db.scalars(select(Organization)))
    jobs={};outbox_pending=0;oldest_outbox=None;active_scorers=0;stale_scorers=0;active_courts=0
    for org in orgs:
        set_scope(org.id)
        for state,count in db.execute(select(Job.status,func.count()).where(Job.organization_id==org.id).group_by(Job.status)):
            jobs[state]=jobs.get(state,0)+count
        outbox_pending+=db.scalar(select(func.count()).select_from(Outbox).where(Outbox.organization_id==org.id,Outbox.published_at.is_(None))) or 0
        oldest=db.scalar(select(func.min(Outbox.created_at)).where(Outbox.organization_id==org.id,Outbox.published_at.is_(None)))
        if oldest and (oldest_outbox is None or oldest<oldest_outbox): oldest_outbox=oldest
        active_scorers+=db.scalar(select(func.count()).select_from(ScorerSession).where(ScorerSession.organization_id==org.id,ScorerSession.revoked_at.is_(None),ScorerSession.expires_at>now())) or 0
        stale_scorers+=db.scalar(select(func.count()).select_from(ScorerSession).where(ScorerSession.organization_id==org.id,ScorerSession.revoked_at.is_(None),ScorerSession.expires_at>now(),ScorerSession.last_seen_at<now()-timedelta(seconds=30))) or 0
        active_courts+=db.scalar(select(func.count()).select_from(CourtAssignment).where(CourtAssignment.organization_id==org.id,CourtAssignment.released_at.is_(None))) or 0
    engine=current_app.extensions['db_engine'];pool=engine.pool
    checked_out,size,overflow=pool.checkedout(),pool.size(),pool.overflow()
    capacity=size+max(0,overflow)
    storage=Path(current_app.config.get('PRIVATE_STORAGE_PATH',Path(current_app.instance_path)/'private'))
    storage.mkdir(parents=True,exist_ok=True,mode=0o700);space=shutil.disk_usage(storage)
    return {'generated_at':iso(now()),'organizations':len(orgs),'jobs':jobs,
            'outbox':{'pending':outbox_pending,'oldest_pending_at':iso(oldest_outbox)},
            'scorers':{'active_sessions':active_scorers,'stale_heartbeat_sessions':stale_scorers,'stale_after_seconds':30},
            'court_assignments':{'unreleased':active_courts},
            'database_pool':{'size':size,'checked_out':checked_out,'overflow':overflow,
                'utilization_percent':round(checked_out/capacity*100,2) if capacity else 0,
                'scope':'current_application_process',
                'utilization_definition':'Checked-out connections divided by configured pool size plus current positive overflow.'},
            'private_storage':{'adapter':'configured-local-filesystem','free_bytes':space.free,'total_bytes':space.total},
            'backup':backup_status(),'performance':performance_snapshot(),'service':service_snapshot(),
            'monitoring':'Current request samples and cumulative database counters only; historical monitoring, alerts and independent backup verification must be configured.',
            'recovery_targets':{'proposed_rpo_minutes':5,'proposed_rto_minutes':60,'measured':False}}
