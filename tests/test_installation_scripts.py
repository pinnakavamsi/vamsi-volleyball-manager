"""Fresh-install guards run with disposable files and mocked PostgreSQL processes."""
import importlib.util
from pathlib import Path
import subprocess
import sys

from cryptography.fernet import Fernet
from dotenv import dotenv_values
import psycopg
import pytest
from sqlalchemy.engine import make_url

ROOT=Path(__file__).resolve().parents[1]

def local_script(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('local_database_under_test',ROOT/'scripts/local_database.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.ROOT=tmp_path;module.INSTANCE=tmp_path/'instance';module.CLUSTER=module.INSTANCE/'postgres'
    monkeypatch.setattr(module,'postgres_bin',lambda:tmp_path/'postgres-tools')
    monkeypatch.setattr(sys,'argv',['local_database.py','--port','55433'])
    return module

def test_fresh_local_install_generates_only_private_infrastructure(tmp_path,monkeypatch):
    module=local_script(tmp_path,monkeypatch);operations=[];commands=[]
    class Probe:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def connect_ex(self,address):assert address==('127.0.0.1',55433);return 1
    monkeypatch.setattr(module.socket,'socket',lambda:Probe())
    def run(command,**options):
        commands.append(command)
        if Path(command[0]).name=='initdb':
            module.CLUSTER.mkdir();(module.CLUSTER/'PG_VERSION').write_text('18')
            assert '--auth-local=scram-sha-256' in command and '--auth-host=scram-sha-256' in command
            assert (module.INSTANCE/'pg-init-password').stat().st_mode&0o777==0o600
        return subprocess.CompletedProcess(command,3 if command[-1]=='status' else 0)
    monkeypatch.setattr(module.subprocess,'run',run)
    class Result:
        def fetchone(self):return None
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,statement,parameters=None):
            text=statement if isinstance(statement,str) else statement.as_string()
            operations.append(' '.join(text.split()[:3]));return Result()
    monkeypatch.setattr(psycopg,'connect',lambda *args,**kwargs:Connection())
    module.main();config=dotenv_values(tmp_path/'.env')
    assert (tmp_path/'.env').stat().st_mode&0o777==0o600
    assert not (module.INSTANCE/'pg-init-password').exists()
    assert not (module.INSTANCE/'demo-access.txt').exists()
    assert not (module.INSTANCE/'demo.json').exists()
    urls=[make_url(config[name]) for name in ('DATABASE_URL','MIGRATION_DATABASE_URL','BACKUP_DATABASE_URL')]
    assert all(url.host=='127.0.0.1' and url.port==55433 and url.database=='volleyball' for url in urls)
    assert len({url.password for url in urls})==3 and all(len(url.password)>=32 for url in urls)
    assert len(config['SECRET_KEY'])==96;Fernet(config['MFA_ENCRYPTION_KEY'].encode())
    assert config['GEOCODING_LICENSE_CONFIRMED']==config['WEATHER_LICENSE_CONFIRMED']=='false'
    assert set(config).isdisjoint({'ADMIN_EMAIL','ADMIN_PASSWORD','PLATFORM_EMAIL','PLATFORM_PASSWORD','ORGANIZER_EMAIL','ORGANIZER_PASSWORD'})
    assert not any(operation.startswith(('INSERT','UPDATE','TRUNCATE','DELETE')) for operation in operations)
    assert any(operation.startswith('CREATE ROLE') for operation in operations)
    assert any(operation.startswith('CREATE DATABASE') for operation in operations)
    assert any("unix_socket_directories=''" in ' '.join(command) for command in commands)

def test_existing_configuration_on_another_project_port_is_preserved(tmp_path,monkeypatch):
    module=local_script(tmp_path,monkeypatch);module.CLUSTER.mkdir(parents=True);(module.CLUSTER/'PG_VERSION').write_text('18')
    environment='DATABASE_URL=postgresql+psycopg://volleyball_app:fixture@127.0.0.1:55432/volleyball\nMIGRATION_DATABASE_URL=postgresql+psycopg://volleyball_owner:fixture@127.0.0.1:55432/volleyball\n'
    (tmp_path/'.env').write_text(environment)
    monkeypatch.setattr(module.subprocess,'run',lambda *args,**kwargs:pytest.fail('Existing foreign port must not start PostgreSQL.'))
    monkeypatch.setattr(psycopg,'connect',lambda *args,**kwargs:pytest.fail('Existing foreign port must not connect.'))
    with pytest.raises(SystemExit,match='does not target this project cluster'):module.main()
    assert (tmp_path/'.env').read_text()==environment

def test_explicit_demo_script_requires_confirmation_before_password_or_database(tmp_path):
    result=subprocess.run([sys.executable,str(ROOT/'scripts/prepare_demo.py'),'--email','organizer@example.invalid'],cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode==2 and '--confirm-isolated-database is required' in result.stderr
    assert not (tmp_path/'instance').exists()

def test_local_listener_uses_only_the_selected_loopback_origin(monkeypatch,tmp_path):
    from scripts import start_local
    monkeypatch.setattr(start_local,'ROOT',tmp_path)
    monkeypatch.setenv('PUBLIC_BASE_URL','http://localhost:5051/')
    assert start_local.local_address()==('127.0.0.1',5051,'http://localhost:5051')
    for origin in ['https://example.com:5051','http://127.0.0.1','http://127.0.0.1:5051/private','http://user:password@127.0.0.1:5051']:
        monkeypatch.setenv('PUBLIC_BASE_URL',origin)
        with pytest.raises(SystemExit):start_local.local_address()

def test_restore_drill_requires_disposable_source_confirmation_before_credentials(tmp_path):
    result=subprocess.run([sys.executable,str(ROOT/'scripts/validate_restore.py'),'--source-cluster',str(tmp_path/'postgres'),'--email','organizer@example.invalid','--organization-id','00000000-0000-4000-8000-000000000001','--match-id','00000000-0000-4000-8000-000000000002'],cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode==2 and '--confirm-disposable-source is required' in result.stderr
    assert not (tmp_path/'postgres').exists()
