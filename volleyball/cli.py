import json,os
from pathlib import Path
import click
from flask import current_app
from sqlalchemy import create_engine,select,text
from sqlalchemy.orm import Session
from .db import get_db,set_scope
from .models import Organization,Outbox,Token

def init_cli(app):
    @app.cli.command('bootstrap-admin')
    @click.option('--email',required=True)
    @click.option('--name',default='Platform administrator')
    @click.option('--password',prompt=True,hide_input=True,confirmation_prompt=True)
    def bootstrap(email,name,password):
        """Provision the first platform admin with an explicitly supplied password."""
        from .auth import bootstrap_admin
        owner=os.environ.get('MIGRATION_DATABASE_URL')
        if not owner:raise click.ClickException('MIGRATION_DATABASE_URL required for secure bootstrap.')
        engine=create_engine(owner)
        with Session(engine) as db:
            user=bootstrap_admin(db,email,password,name);db.commit()
        engine.dispose();click.echo('Administrator created; enroll MFA after first sign in.')
    @app.cli.command('seed-demo')
    @click.option('--email',required=True)
    @click.option('--password',prompt=True,hide_input=True,confirmation_prompt=True)
    @click.option('--confirm-isolated-database',is_flag=True,help='Confirm this separately configured demo/test database may receive synthetic fixtures.')
    def seed_demo(email,password,confirm_isolated_database):
        """Seed clearly identified development data in this isolated database."""
        if app.config['APP_ENV'] not in ('development','test'):raise click.ClickException('Demo seed requires a development or test installation.')
        if not confirm_isolated_database:raise click.ClickException('Pass --confirm-isolated-database only for a separate disposable demo/test database.')
        app.config['ALLOW_DEMO_SEED']=True
        from .seed import seed_demo as seed
        result=seed(get_db(),email,password);get_db().commit();click.echo(json.dumps(result,indent=2))
    @app.cli.command('deliver-emails')
    def emails():
        from .auth import deliver_pending_identity_emails
        click.echo(str(deliver_pending_identity_emails()))
    @app.cli.command('email-outbox')
    def email_outbox():
        if app.config['APP_ENV'] not in ('development','test'):raise click.ClickException('Local outbox is development only.')
        path=Path(app.instance_path)/'email-outbox.jsonl'
        if not path.exists():click.echo('No local messages. Configure EMAIL_DELIVERY_MODE=file and run deliver-emails.');return
        click.echo(path.read_text())
    @app.cli.command('verify-projections')
    @click.option('--organization-id',required=True)
    def verify_projections(organization_id):
        from .models import Match
        from .services.recovery import journal_projection
        set_scope(organization_id);db=get_db()
        rows=[journal_projection(m) for m in db.scalars(select(Match).where(Match.organization_id==organization_id))]
        click.echo(json.dumps([{k:v for k,v in r.items() if k not in ('state','rule_snapshot','lifecycle')} for r in rows],indent=2))
    @app.cli.command('rebuild-match')
    @click.option('--organization-id',required=True)
    @click.option('--match-id',required=True)
    @click.option('--confirm',is_flag=True)
    def rebuild_match(organization_id,match_id,confirm):
        from .models import Match
        from .services.recovery import rebuild
        from .common import audit
        if not confirm:raise click.ClickException('Inspect verify-projections first, then pass --confirm to apply the reviewed rebuild.')
        set_scope(organization_id);db=get_db()
        match=db.scalar(select(Match).where(Match.organization_id==organization_id,Match.id==match_id).with_for_update())
        if not match:raise click.ClickException('Match unavailable in this organization.')
        result=rebuild(match);audit('projection.rebuilt','match',match.id,reason='Explicit operator recovery from accepted journal');db.commit()
        click.echo('Projection rebuilt from '+str(result['events'])+' immutable events.')
    @app.cli.command('publish-outbox')
    def publish():
        """Mark bounded-polling delivery readiness after committed journal writes."""
        from .common import now
        count=0;db=get_db()
        for org in db.scalars(select(Organization).where(Organization.active.is_(True))).all():
            set_scope(org.id)
            for event in db.scalars(select(Outbox).where(Outbox.published_at.is_(None)).order_by(Outbox.created_at).limit(1000).with_for_update(skip_locked=True)):
                event.published_at=now();event.attempts+=1;count+=1
            db.commit()
        click.echo(f'{count} committed events available to durable polling consumers.')
