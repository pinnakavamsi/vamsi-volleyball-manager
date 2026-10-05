"""Durable export/email publication and bounded expired-throttle maintenance."""
import time
from sqlalchemy import select,delete
from . import create_app
from .db import get_db,set_scope
from .models import Organization,Outbox,Token
from .common import now
from .reports import process_export_jobs
from .auth import deliver_pending_identity_emails


def run_once():
    db=get_db();count=process_export_jobs(limit=10)
    deliver_pending_identity_emails(db)
    for org in db.scalars(select(Organization).where(Organization.active.is_(True))).all():
        set_scope(org.id)
        for item in db.scalars(select(Outbox).where(Outbox.published_at.is_(None)).limit(1000).with_for_update(skip_locked=True)):
            item.published_at=now();item.attempts+=1
        db.commit()
    expired_limits=select(Token.id).where(Token.data.has_key('throttle_key'),Token.expires_at<now()).limit(1000)
    db.execute(delete(Token).where(Token.id.in_(expired_limits)))
    db.commit();return count

def main():
    app=create_app()
    while True:
        with app.app_context():
            try:run_once()
            except Exception as error:
                get_db().rollback();app.logger.error('worker_batch_failed type=%s',type(error).__name__)
        time.sleep(2)

if __name__=='__main__':main()
