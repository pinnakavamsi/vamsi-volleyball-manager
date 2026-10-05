"""Create review accounts with random credentials saved privately, never hardcoded."""
import sys,secrets,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select,create_engine
from sqlalchemy.orm import Session
from volleyball import create_app
from volleyball.db import get_db
from volleyball.models import Organization
from volleyball.seed import seed_demo
from volleyball.auth import bootstrap_admin
import os
root=Path(__file__).resolve().parents[1]
app=create_app({'ALLOW_DEMO_SEED':True})
with app.app_context():
    if get_db().scalar(select(Organization).where(Organization.slug=='hudson-demo')):
        print('Existing isolated demo data preserved.')
        raise SystemExit(0)
    email='organizer@volleyball.test';password=secrets.token_urlsafe(20)
    result=seed_demo(get_db(),email,password);get_db().commit()
    platform_password=secrets.token_urlsafe(24)
    engine=create_engine(os.environ['MIGRATION_DATABASE_URL'])
    with Session(engine) as owner:
        bootstrap_admin(owner,'platform@volleyball.test',platform_password,'Local platform administrator');owner.commit()
    engine.dispose()
    access=root/'instance'/'demo-access.txt'
    access.write_text('LOCAL DEVELOPMENT ACCOUNTS — isolated demo data\n\nOrganizer email: '+email+'\nOrganizer password: '+password+'\n\nPlatform email: platform@volleyball.test\nPlatform password: '+platform_password+'\n\nEnroll MFA in Profile and security. Credentials are random and are not production accounts.\n')
    access.chmod(0o600)
    (root/'instance'/'demo.json').write_text(json.dumps(result,indent=2))
    print('Isolated demo seeded. Random local account credentials saved to instance/demo-access.txt.')
