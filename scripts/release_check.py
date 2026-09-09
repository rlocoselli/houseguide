"""Check launch configuration without printing credentials."""
import os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()
required=['SECRET_KEY','PUBLIC_ORIGIN','LEGAL_ENTITY_NAME','LEGAL_ADDRESS','LEGAL_REGISTRATION','PRIVACY_EMAIL','SUPPORT_EMAIL','SMTP_HOST','MAIL_FROM','STRIPE_SECRET_KEY','STRIPE_PRICE_ID','STRIPE_WEBHOOK_SECRET','BACKUP_ENCRYPTION_KEY']
missing=[name for name in required if not os.getenv(name)]
if not os.getenv('PUBLIC_ORIGIN','').startswith('https://'):missing.append('PUBLIC_ORIGIN must be HTTPS')
if os.getenv('HTTPS')!='1':missing.append('HTTPS=1')
if os.getenv('BILLING_REQUIRED')!='1':missing.append('BILLING_REQUIRED=1')
if not os.getenv('DATABASE_URL') and not all(os.getenv(k) for k in ('POSTGRES_HOST','POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD')):missing.append('PostgreSQL configuration')
if missing:
    print('Launch pending:');print('\n'.join('- '+name for name in missing));sys.exit(1)
print('Configuration present. Also verify DNS, restore drill, providers and legal review before launch.')
