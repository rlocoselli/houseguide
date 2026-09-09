"""Atomic PostgreSQL + upload archive with optional streaming authenticated encryption."""
import os, sys, shutil, subprocess, tempfile, tarfile, time, hashlib, base64
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy.engine import URL
from database import database_url,engine
from sqlalchemy import text
from services import upload_dir
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes

def encrypt(source,destination,key):
    decoded=base64.urlsafe_b64decode(key)
    if len(decoded)!=32: raise ValueError('BACKUP_ENCRYPTION_KEY must encode 32 bytes')
    nonce=os.urandom(12);cipher=Cipher(algorithms.AES(decoded),modes.GCM(nonce)).encryptor();cipher.authenticate_additional_data(b'HGBAK1')
    with open(source,'rb') as src,open(destination,'wb') as dest:
        dest.write(b'HGBAK1'+nonce)
        for block in iter(lambda:src.read(1024*1024),b''):dest.write(cipher.update(block))
        dest.write(cipher.finalize());dest.write(cipher.tag)

def backup():
    root=Path(os.getenv('BACKUP_DIR',str(Path(__file__).resolve().parents[1]/'.local/ops-backups')));root.mkdir(parents=True,exist_ok=True);root.chmod(0o700)
    binary=os.getenv('PG_DUMP_BIN') or shutil.which('pg_dump') or str(Path(__file__).resolve().parents[1]/'node_modules/@embedded-postgres/linux-x64/native/bin/pg_dump')
    url=database_url()
    if url.get_backend_name()!='postgresql':raise RuntimeError('Backups require PostgreSQL')
    public=URL.create('postgresql',username=url.username,host=url.host,port=url.port,database=url.database,query=url.query)
    env=dict(os.environ,PGPASSWORD=url.password or '')
    if os.getenv('PG_CLIENT_LIB_DIR'):env['LD_LIBRARY_PATH']=os.environ['PG_CLIENT_LIB_DIR']
    target=root/('houseguide-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'.tar.gz')
    with engine.connect() as lock:
        lock.execute(text('SELECT pg_advisory_lock(741903)'))
        try:
            with tempfile.TemporaryDirectory(dir=root) as temporary:
                temp=Path(temporary)
                result=subprocess.run([binary,'--format=custom','--no-owner','--file',str(temp/'database.dump'),'--dbname',public.render_as_string()],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                if result.returncode:raise RuntimeError('pg_dump failed; verify connectivity and client version')
                with tarfile.open(temp/'archive.tar.gz','w:gz') as archive:
                    archive.add(temp/'database.dump',arcname='database.dump')
                    archive.add(upload_dir(),arcname='uploads',filter=lambda info: info if info.isfile() or info.isdir() else None)
                key=os.getenv('BACKUP_ENCRYPTION_KEY')
                if key:
                    target=Path(str(target)+'.enc');encrypt(temp/'archive.tar.gz',temp/'encrypted',key);(temp/'encrypted').chmod(0o600);os.replace(temp/'encrypted',target)
                else:
                    (temp/'archive.tar.gz').chmod(0o600);os.replace(temp/'archive.tar.gz',target)
        finally:lock.execute(text('SELECT pg_advisory_unlock(741903)'))
    checksum=hashlib.sha256()
    with open(target,'rb') as archive:
        for block in iter(lambda:archive.read(1024*1024),b''):checksum.update(block)
    digest=checksum.hexdigest()
    Path(str(target)+'.sha256').write_text(digest+'\n')
    if os.getenv('BACKUP_S3_BUCKET'):
        if not os.getenv('BACKUP_ENCRYPTION_KEY'):raise RuntimeError('Offsite backups require encryption')
        import boto3
        client=boto3.client('s3',endpoint_url=os.getenv('S3_ENDPOINT_URL') or None)
        client.upload_file(str(target),os.environ['BACKUP_S3_BUCKET'],'houseguide/'+target.name,ExtraArgs={'ServerSideEncryption':'AES256'})
        from services import write_erasure_ledger
        write_erasure_ledger()
    days=max(1,int(os.getenv('BACKUP_RETENTION_DAYS','30')))
    for old in root.glob('houseguide-*.tar.gz*'):
        if old.stat().st_mtime<time.time()-days*86400:old.unlink()
    print('Backup completed:',target.name)
    return target
if __name__=='__main__':backup()
