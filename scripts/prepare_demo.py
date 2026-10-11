"""Explicitly seed synthetic fixtures into a separately configured demo/test database."""
import argparse
import getpass
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--email',required=True,help='Synthetic organizer email ending in .test or .invalid.')
    parser.add_argument('--confirm-isolated-database',action='store_true',help='Confirm DATABASE_URL targets a separate disposable demo/test database.')
    args=parser.parse_args()
    if not args.confirm_isolated_database:parser.error('Explicit --confirm-isolated-database is required. Normal startup never seeds fixtures.')
    password=getpass.getpass('Choose a synthetic fixture password: ')
    if password!=getpass.getpass('Confirm fixture password: '):raise SystemExit('Passwords do not match. No fixtures were created.')
    from volleyball import create_app
    from volleyball.db import get_db
    from volleyball.seed import seed_demo
    app=create_app({'ALLOW_DEMO_SEED':True})
    with app.app_context():
        result=seed_demo(get_db(),args.email,password)
        get_db().commit()
    print(json.dumps(result,indent=2))
    print('Synthetic demo fixtures created in the separate demo/test database. No platform administrator or saved login credentials were created.')

if __name__=='__main__':main()
