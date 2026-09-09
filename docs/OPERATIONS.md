# Operação — House Guide / Audela de donnees

Domínio: houseguide.audeladedonnees.fr. Contato: admin@audeladedonnees.fr.
Aplicação monolítica Flask + frontend compilado, PostgreSQL e armazenamento persistente de fotos. O processo operations é um agendador do mesmo código, não uma API separada.

## Publicação

1. Definir servidor, apontar DNS A/AAAA e permitir portas 80/443.
2. Preencher .env: identidade legal completa, SMTP, Stripe e segredo de backup. Configurar Google, Kimi e Geoapify para ativá-los.
3. Autorizar https://houseguide.audeladedonnees.fr no console Google.
4. Na Stripe, criar preço recorrente **EUR 19 / ano / propriedade**, ativar portal do cliente e cadastrar webhook https://houseguide.audeladedonnees.fr/api/billing/webhook com checkout.session.completed e customer.subscription.created/updated/deleted.
5. Executar:

       docker compose -f compose.yaml -f compose.production.yaml up --build -d

6. Caddy solicita e renova o certificado após DNS correto. O override esconde a porta 8000. Requer Docker Compose com suporte à tag !reset.
7. Verificar readiness, cadastro, e-mail, upload, pagamento em modo de teste, publicação e restauração antes de aceitar clientes.

release_check.py exige configuração mínima. Variáveis preenchidas não comprovam contratos dos provedores ou conformidade jurídica. Docker/HTTPS não foram executados nesta máquina, que não possui Docker.

## Volumes

postgres_data: banco; uploads: fotos; backups: arquivos diários; erasures: registro mínimo de IDs apagados, separado dos snapshots; caddy_data: certificados.

Não use docker compose down -v para atualizar. O backup não inclui .env: guarde segredos em cofre separado. Perder a chave impede a restauração dos arquivos criptografados.

## Backup e restauração

O job verifica /health/ready a cada minuto e executa manutenção e backup diário. Registra falhas e pode avisar por ALERT_WEBHOOK_URL. Não há monitoramento externo contratado neste projeto.

    .venv/bin/python scripts/maintenance.py
    .venv/bin/python scripts/backup.py

É necessário pg_dump compatível com o servidor ou superior. Configure PG_DUMP_BIN e PG_RESTORE_BIN quando não estiverem no PATH. O PostgreSQL embutido não inclui esses clientes.

As APIs modificadoras usam lock compartilhado e o backup usa lock exclusivo durante snapshot do banco e fotos; leituras continuam. Escritas podem aguardar nesse período.

BACKUP_ENCRYPTION_KEY contém 32 bytes em base64url. A criptografia AES-256-GCM é feita em streaming e autenticada antes da extração. Produção exige chave; arquivos locais têm permissões restritas. Retenção padrão: 30 dias.

Para cópia externa, configurar BACKUP_S3_BUCKET, credenciais AWS e opcional S3_ENDPOINT_URL. O bucket deve ser privado, aceitar SSE AES256 e possuir ciclo de vida equivalente à retenção. A cópia externa exige criptografia e não foi validada contra bucket real. O registro de exclusões é sincronizado separadamente.

Restaure **primeiro em banco e diretório de fotos descartáveis**, com a aplicação parada:

    DATABASE_URL=postgresql+psycopg://.../restore_test UPLOAD_DIR=/isolated/uploads \
      .venv/bin/python scripts/restore.py /backup/houseguide-....tar.gz.enc \
      --confirm-replace --erasure-log /current/erasure-log.jsonl

O comando substitui o banco de destino e as fotos desse diretório. Use somente arquivos confiáveis. O ledger atual é obrigatório em produção para reaplicar exclusões e impedir reativação de contas apagadas. A sequência de IDs é avançada. Após restauração, reconcilie assinaturas com Stripe antes de reabrir; snapshots podem ter estado de cobrança antigo. Faça exercícios regulares de restauração.

## Retenção

A manutenção remove tokens expirados, limites com mais de 48h, estatísticas/eventos de webhook com mais de 90 dias, fotos não referenciadas há mais de 24h e registros de exclusão após retenção de backup (mínimo30d) + 7d. Contas não são apagadas por inatividade. Configure ciclos equivalentes em provedores externos.

Guia offline: cópia explícita por até 24h no dispositivo; não pode ser revogada sem conexão.

## Segurança e incidentes

/health/live verifica processo; /health/ready verifica tabelas. Logs incluem rota lógica, status, duração e ID, sem corpo, query string, tokens, senha ou IP em claro. Contêineres têm rotação de 10 MB × 3 arquivos. Configure políticas equivalentes no provedor. Não exponha o backend quando TRUST_PROXY_HOPS=1.

Em incidente: conter acesso afetado; preservar evidências com acesso limitado; registrar linha do tempo; revogar credenciais comprometidas; avaliar titulares/dados; informar admin@audeladedonnees.fr; avaliar notificações legais; corrigir e documentar prevenção.

Consulte [CNIL](https://www.cnil.fr/fr/notifier-une-violation-de-donnees-personnelles) e [ANPD](https://www.gov.br/anpd/pt-br) para obrigações aplicáveis.

## Direitos e pendências

A conta de ADMIN_EMAILS precisa de e-mail verificado para ver a fila administrativa. A fila permite atualizar estados, mas marcar concluída não executa correções nem envia resposta: o responsável precisa cumprir o pedido e responder ao titular. Estabeleça triagem diária e controle dos prazos legais.

Pendentes: servidor/DNS, credenciais reais, endereço/registro legal e avaliação jurídica/contratual. Modelos de documentos e controles técnicos não certificam conformidade integral RGPD/LGPD.
