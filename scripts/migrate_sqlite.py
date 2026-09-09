"""One-time transactional import from the previous MVP; source stays untouched."""
import sys
import json
import sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, insert
from database import engine, init_db, users, properties

def migrate(path):
    init_db()
    with sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True) as source, engine.begin() as target:
        source.row_factory = sqlite3.Row
        if target.execute(select(users.c.id).limit(1)).first() or target.execute(select(properties.c.code).limit(1)).first():
            raise RuntimeError('Destination must be empty. No data was changed.')
        owner_map = {}
        for row in source.execute('SELECT * FROM users'):
            owner_map[row['id']] = target.execute(insert(users).values(email=row['email'], password=row['password'], name=row['name']).returning(users.c.id)).scalar_one()
        count = 0
        for row in source.execute('SELECT * FROM properties'):
            target.execute(insert(properties).values(code=row['code'], owner=owner_map[row['owner']], data=json.loads(row['data'])))
            count += 1
    print(f'Imported {len(owner_map)} accounts and {count} properties. Source database unchanged.')
if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit('Usage: python scripts/migrate_sqlite.py path/to/houseguide.sqlite3')
    migrate(sys.argv[1])
