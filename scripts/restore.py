"""Restore into a disposable/maintenance database only; explicit confirmation required."""
import os,sys,base64,tempfile,tarfile,subprocess,shutil,argparse,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from database import database_url,engine,users,properties,photos,subscriptions,profiles,action_tokens,ai_usage,guide_stats,privacy_requests
from sqlalchemy.engine import URL
from sqlalchemy import delete,select,text
from services import upload_dir

def decrypt(source,target,key):
    decoded=base64.urlsafe_b64decode(key)
    with open(source,'rb') as src:
        if src.read(6)!=b'HGBAK1':raise ValueError('Unknown archive format')
        nonce=src.read(12);src.seek(-16,2);tag=src.read(16);size=src.tell()-34;src.seek(18)
        cipher=Cipher(algorithms.AES(decoded),modes.GCM(nonce,tag)).decryptor();cipher.authenticate_additional_data(b'HGBAK1')
        with open(target,'wb') as out:
            while size:
                data=src.read(min(size,1024*1024));size-=len(data);out.write(cipher.update(data))
            out.write(cipher.finalize())

def restore(archive,confirm=False,erasure_log=None):
    if not confirm:raise RuntimeError('Pass --confirm-replace after stopping the app and choosing the target database.')
    url=database_url()
    if url.get_backend_name()!='postgresql':raise RuntimeError('PostgreSQL required')
    if os.getenv('PUBLIC_LAUNCH')=='1' and not erasure_log:raise RuntimeError('Production restore requires --erasure-log with the current deletion ledger')
    public=URL.create('postgresql',username=url.username,host=url.host,port=url.port,database=url.database,query=url.query)
    binary=os.getenv('PG_RESTORE_BIN') or shutil.which('pg_restore') or str(Path(__file__).resolve().parents[1]/'node_modules/@embedded-postgres/linux-x64/native/bin/pg_restore')
    env=dict(os.environ,PGPASSWORD=url.password or '')
    if os.getenv('PG_CLIENT_LIB_DIR'):env['LD_LIBRARY_PATH']=os.environ['PG_CLIENT_LIB_DIR']
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);source=Path(archive)
        if source.suffix=='.enc':
            decrypt(source,root/'archive.tar.gz',os.environ['BACKUP_ENCRYPTION_KEY']);source=root/'archive.tar.gz'
        with tarfile.open(source) as tar:
            # Explicit allowlist: never extract links or arbitrary paths.
            for member in tar.getmembers():
                path=Path(member.name)
                if member.name!='database.dump' and (not path.parts or path.parts[0]!='uploads'):raise ValueError('Unexpected archive member')
                if '..' in path.parts or path.is_absolute() or not(member.isfile() or member.isdir()):raise ValueError('Unsafe archive member')
            tar.extractall(root)
        result=subprocess.run([binary,'--clean','--if-exists','--no-owner','--exit-on-error','--dbname',public.render_as_string(),str(root/'database.dump')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        if result.returncode:raise RuntimeError('pg_restore failed; target must remain offline for inspection')
        dest=upload_dir()
        for file in dest.glob('*.webp'):file.unlink()
        for file in (root/'uploads').glob('*.webp'):shutil.copy2(file,dest/file.name)
    if erasure_log:
        ids={json.loads(line)['owner'] for line in Path(erasure_log).read_text().splitlines() if line.strip()}
        with engine.begin() as c:
            for uid in ids:
                codes=select(properties.c.code).where(properties.c.owner==uid)
                image_ids=list(c.execute(select(photos.c.id).where(photos.c.owner==uid)).scalars())
                c.execute(delete(guide_stats).where(guide_stats.c.code.in_(codes)))
                for table in (subscriptions,privacy_requests,action_tokens,profiles,photos,ai_usage,properties):c.execute(delete(table).where(table.c.owner==uid))
                c.execute(delete(users).where(users.c.id==uid))
                for image_id in image_ids:(upload_dir()/(image_id+'.webp')).unlink(missing_ok=True)
        with engine.begin() as c:
            highest=max(ids,default=0)
            c.execute(text("SELECT setval(pg_get_serial_sequence('users','id'), GREATEST(COALESCE((SELECT MAX(id) FROM users),0), :highest, 1), true)"),{'highest':highest})
    print('Restore completed. Reconcile subscriptions, review erasures and verify before restarting the app.')
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('archive');parser.add_argument('--confirm-replace',action='store_true');parser.add_argument('--erasure-log');args=parser.parse_args();restore(args.archive,args.confirm_replace,args.erasure_log)
