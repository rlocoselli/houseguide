"""Retention job. Does not delete accounts or published guide content."""
import sys,time,os
from pathlib import Path
from datetime import datetime,timedelta,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database import engine,action_tokens,limits,guide_stats,webhook_events,photos,properties,users
from services import upload_dir
from sqlalchemy import delete,select
def maintain():
    now=int(time.time())
    with engine.begin() as c:
        c.execute(delete(action_tokens).where(action_tokens.c.expires<now))
        c.execute(delete(limits).where(limits.c.window<now//3600-48))
        c.execute(delete(guide_stats).where(guide_stats.c.day<(datetime.now(timezone.utc)-timedelta(days=90)).date().isoformat()))
        c.execute(delete(webhook_events).where(webhook_events.c.created<now-90*86400))
    # Lock each owner, as property edits do, to avoid removing a newly attached image.
    with engine.connect() as c:owners=list(c.execute(select(users.c.id)).scalars())
    for owner in owners:
        removed=[]
        with engine.begin() as c:
            c.execute(select(users.c.id).where(users.c.id==owner).with_for_update()).first()
            refs=set()
            for data in c.execute(select(properties.c.data).where(properties.c.owner==owner)).scalars():
                refs.update([data.get('image','')]+data.get('images',[]))
            for row in c.execute(select(photos).where((photos.c.owner==owner)&(photos.c.created<now-86400))).mappings():
                if '/media/'+row['id'] not in refs:
                    c.execute(delete(photos).where(photos.c.id==row['id']));removed.append(row['id'])
        for photo_id in removed:(upload_dir()/(photo_id+'.webp')).unlink(missing_ok=True)
    from database import erasures
    with engine.begin() as c:c.execute(delete(erasures).where(erasures.c.created<now-(max(30,int(os.getenv('BACKUP_RETENTION_DAYS','30')))+7)*86400))
    from services import write_erasure_ledger
    write_erasure_ledger()
    print('Retention maintenance completed.')
if __name__=='__main__':maintain()
