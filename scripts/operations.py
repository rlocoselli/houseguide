"""Daily backups/retention and readiness checks. Failures are visible in container logs."""
import os,time,logging
import requests
from backup import backup
from maintenance import maintain
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
last_backup=0
while True:
    try:
        response=requests.get(os.getenv('HEALTHCHECK_URL','http://app:8000/health/ready'),timeout=10)
        if response.status_code!=200:raise RuntimeError('readiness_failed')
        if time.time()-last_backup>86400:
            maintain();backup();last_backup=time.time()
        logging.info('houseguide_ops healthy')
    except Exception as error:
        logging.error('houseguide_ops failure type=%s',type(error).__name__)
        # Optional monitoring integration, configured explicitly by the operator.
        if os.getenv('ALERT_WEBHOOK_URL'):
            try:requests.post(os.environ['ALERT_WEBHOOK_URL'],json={'service':'houseguide','status':'unhealthy'},timeout=10)
            except requests.RequestException:pass
    time.sleep(60)
