"""The monolith's PostgreSQL persistence; SQLite is allowed only in tests."""
import os
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Text, ForeignKey, JSON
from sqlalchemy.engine import URL, make_url
from dotenv import load_dotenv

load_dotenv()

def database_url():
    raw = os.environ.get('DATABASE_URL')
    if raw:
        url = make_url(raw)
        if url.drivername in ('postgres', 'postgresql'):
            url = url.set(drivername='postgresql+psycopg')
        if url.get_backend_name() != 'postgresql' and not (os.getenv('TESTING') == '1' and url.get_backend_name() == 'sqlite'):
            raise RuntimeError('DATABASE_URL must use PostgreSQL. SQLite is supported only with TESTING=1.')
        return url
    required = ('POSTGRES_HOST', 'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD')
    if not all(os.getenv(k) for k in required):
        raise RuntimeError('Set DATABASE_URL or POSTGRES_HOST, POSTGRES_DB, POSTGRES_USER and POSTGRES_PASSWORD in .env.')
    return URL.create('postgresql+psycopg', username=os.environ['POSTGRES_USER'], password=os.environ['POSTGRES_PASSWORD'], host=os.environ['POSTGRES_HOST'], port=int(os.getenv('POSTGRES_PORT', '5432')), database=os.environ['POSTGRES_DB'], query={'sslmode': os.getenv('POSTGRES_SSLMODE', 'prefer')})

engine = create_engine(database_url(), pool_pre_ping=True)
metadata = MetaData()
users = Table('users', metadata,
    Column('id', Integer, primary_key=True),
    Column('email', String(320), nullable=False, unique=True),
    Column('password', Text),
    Column('name', String(100), nullable=False),
    Column('google_sub', String(255), unique=True))
properties = Table('properties', metadata,
    Column('code', String(32), primary_key=True),
    Column('owner', Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
    Column('data', JSON, nullable=False))
# Database-backed rate limit, shared across Gunicorn workers.
ai_usage = Table('ai_usage', metadata,
    Column('owner', Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    Column('window', Integer, nullable=False),
    Column('count', Integer, nullable=False))

def init_db():
    metadata.create_all(engine)

# Additive v2 schema: existing user/property rows are preserved.
from sqlalchemy import Boolean
profiles = Table('profiles', metadata,
    Column('owner', Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    Column('verified', Boolean, nullable=False, default=False),
    Column('session_version', Integer, nullable=False, default=0),
    Column('terms_version', String(40), nullable=False, default=''),
    Column('accepted_at', Integer, nullable=False, default=0))
action_tokens = Table('action_tokens', metadata,
    Column('digest', String(64), primary_key=True),
    Column('owner', Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
    Column('purpose', String(20), nullable=False), Column('expires', Integer, nullable=False))
limits = Table('request_limits', metadata,
    Column('key', String(64), primary_key=True), Column('window', Integer, primary_key=True),
    Column('count', Integer, nullable=False))
photos = Table('photos', metadata,
    Column('id', String(64), primary_key=True), Column('owner', Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
    Column('created', Integer, nullable=False), Column('bytes', Integer, nullable=False))
subscriptions = Table('subscriptions', metadata,
    Column('code', String(32), ForeignKey('properties.code', ondelete='CASCADE'), primary_key=True),
    Column('owner', Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
    Column('subscription_id', String(255), unique=True), Column('customer_id', String(255)),
    Column('status', String(40), nullable=False, default='none'), Column('paid_until', Integer, nullable=False, default=0),
    Column('cancel_at_period_end', Boolean, nullable=False, default=False),
    Column('checkout_id', String(255)), Column('checkout_url', Text), Column('checkout_expires', Integer, nullable=False, default=0))
webhook_events = Table('webhook_events', metadata, Column('id', String(255), primary_key=True), Column('created', Integer, nullable=False))
guide_stats = Table('guide_stats', metadata,
    Column('code', String(32), ForeignKey('properties.code', ondelete='CASCADE'), primary_key=True),
    Column('day', String(10), primary_key=True), Column('views', Integer, nullable=False))
privacy_requests = Table('privacy_requests', metadata,
    Column('id', String(32), primary_key=True), Column('owner', Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
    Column('kind', String(30), nullable=False), Column('message', Text, nullable=False),
    Column('created', Integer, nullable=False), Column('status', String(30), nullable=False, default='received'))
erasures = Table('erasures', metadata,
    Column('owner', Integer, primary_key=True), Column('created', Integer, nullable=False))
