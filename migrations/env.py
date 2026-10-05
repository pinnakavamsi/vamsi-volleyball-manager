from logging.config import fileConfig
import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import engine_from_config,pool
from alembic import context
from volleyball.models import Base
load_dotenv(Path(__file__).resolve().parents[1]/'.env')
config=context.config
if config.config_file_name:fileConfig(config.config_file_name)
url=os.environ.get('MIGRATION_DATABASE_URL',os.environ.get('DATABASE_URL'))
if not url:raise RuntimeError('MIGRATION_DATABASE_URL is required.')
config.set_main_option('sqlalchemy.url',url.replace('%','%%'))
target_metadata=Base.metadata
if context.is_offline_mode():
    context.configure(url=url,target_metadata=target_metadata,literal_binds=True,dialect_opts={'paramstyle':'named'})
    with context.begin_transaction():context.run_migrations()
else:
    engine=engine_from_config(config.get_section(config.config_ini_section),prefix='sqlalchemy.',poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection,target_metadata=target_metadata,compare_type=True)
        with context.begin_transaction():context.run_migrations()
