// Optional local PostgreSQL for development; production uses the configured DB.
import EmbeddedPostgres from 'embedded-postgres';
import {existsSync,mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
if(!['localhost','127.0.0.1'].includes(process.env.POSTGRES_HOST))throw Error('db:local requires POSTGRES_HOST=localhost or 127.0.0.1');
if(!process.env.POSTGRES_PASSWORD||!process.env.POSTGRES_USER||!process.env.POSTGRES_DB)throw Error('Set POSTGRES_USER, POSTGRES_PASSWORD and POSTGRES_DB in .env');
const databaseDir=resolve('.local/postgres');mkdirSync(resolve('.local'),{recursive:true});
const pg=new EmbeddedPostgres({databaseDir,user:process.env.POSTGRES_USER,password:process.env.POSTGRES_PASSWORD,port:Number(process.env.POSTGRES_PORT||5432),persistent:true,authMethod:'scram-sha-256',postgresFlags:['-c','listen_addresses=127.0.0.1']});
if(!existsSync(resolve(databaseDir,'PG_VERSION')))await pg.initialise();
await pg.start();
const client=pg.getPgClient('postgres','127.0.0.1');await client.connect();
const found=await client.query('SELECT 1 FROM pg_database WHERE datname=$1',[process.env.POSTGRES_DB]);
if(!found.rowCount)await client.query('CREATE DATABASE "'+process.env.POSTGRES_DB.replaceAll('"','""')+'"');
await client.end();
console.log('House Guide local PostgreSQL is ready. Run npm start in another terminal.');
let stopping=false;
async function stop(){if(stopping)return;stopping=true;await pg.stop();process.exit(0)}
process.on('SIGINT',stop);process.on('SIGTERM',stop);
setInterval(()=>{},60000);
