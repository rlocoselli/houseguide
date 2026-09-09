# House Guide — Audela de donnees

Monólito Flask + React compilado na mesma origem; PostgreSQL; interface em português, italiano, inglês, alemão, francês e espanhol.

## Funcionalidades

- Contas, aceite versionado de termos, verificação de e-mail, recuperação de senha e invalidação de sessões.
- Login Google e vinculação explícita após login recente.
- Propriedades, publicação/rascunhos, QR code para download/impressão.
- Kimi: traduções para os outros cinco idiomas e sugestões de regras, revisadas antes de salvar.
- Fotos JPEG/PNG/WebP de até 8 MB, sem EXIF, galeria e acesso por dono/publicação.
- Restaurantes via Geoapify, com seleção e atribuição.
- Stripe anual por propriedade, portal de faturas/cancelamento, webhooks assinados/idempotentes e controle de publicação.
- Contagens por guia/dia sem identificar visitantes; offline opcional por até 24h.
- Central de privacidade: exportação, correção de nome, pedidos e exclusão com reautenticação.
- Documentos nos seis idiomas, health checks, limites, logs mínimos, backup criptografado, restauração e HTTPS preparado.

## Executar

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    npm ci
    # Se .env ainda não existir:
    cp .env.example .env
    # Preencha PostgreSQL e SECRET_KEY.
    npm run build
    npm run db:local

Em outro terminal: npm start. Abra http://localhost:8000. O PostgreSQL local persiste em .local/postgres; as credenciais/porta estão em .env. O helper Node só é usado em desenvolvimento.

Com PostgreSQL existente, use DATABASE_URL ou POSTGRES_HOST/PORT/DB/USER/PASSWORD. DATABASE_URL tem prioridade. SQLite só é permitido nos testes com TESTING=1.

## Variáveis dos serviços

| Serviço | Variáveis |
| --- | --- |
| Gemini | GEMINI_API_KEY, GEMINI_MODEL, GEMINI_BASE_URL |
| Google | GOOGLE_CLIENT_ID; origem autorizada no console |
| SMTP | SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, MAIL_FROM |
| Stripe | STRIPE_SECRET_KEY, STRIPE_PRICE_ID, STRIPE_WEBHOOK_SECRET, BILLING_REQUIRED |
| Restaurantes | GEOAPIFY_API_KEY |
| Legal | LEGAL_ENTITY_NAME, LEGAL_ADDRESS, LEGAL_REGISTRATION, LEGAL_COUNTRY, PRIVACY_EMAIL, SUPPORT_EMAIL |
| Publicação | PUBLIC_ORIGIN, HTTPS, PUBLIC_LAUNCH, TRUST_PROXY_HOPS |
| Backup | BACKUP_ENCRYPTION_KEY, BACKUP_DIR, BACKUP_RETENTION_DAYS; S3 opcional |
| Administração | ADMIN_EMAILS; a conta precisa de e-mail verificado |

Todas as chaves ficam no backend. O exemplo usa Audela de donnees, admin@audeladedonnees.fr e houseguide.audeladedonnees.fr. Endereço/registro estão pendentes. Sem credenciais, o recurso externo mostra configuração pendente, sem fingir pagamentos, e-mails ou resultados reais.

Stripe exige preço recorrente EUR 1900 centavos/ano. O portal pode cancelar ao fim do período; excluir conta/propriedade cancela imediatamente, sem reembolso automático. Dados de cartão não passam pelo backend.

## Produção e operação

    docker compose -f compose.yaml -f compose.production.yaml up --build -d

Prepara Caddy no domínio fornecido, esconde o acesso direto ao app e verifica configurações de lançamento. Requer servidor/DNS, Docker Compose recente, dados legais e credenciais.

Consulte [operação e restauração](docs/OPERATIONS.md) e [registro de tratamento](docs/DATA-PROTECTION.md). Os documentos distinguem implementação de responsabilidades do operador.

## Testes

    npm run build
    npm test
    # Somente banco VAZIO/DESCARTÁVEL: os testes apagam suas tabelas.
    TEST_DATABASE_URL=postgresql+psycopg://.../houseguide_test npm test

Cobrem contas, fotos, reset, privacidade, assinaturas/webhooks, IA e criptografia dos backups. Provedores são simulados.

Navegador com banco separado:

    TESTING=1 DATABASE_URL=sqlite:////tmp/houseguide-browser-v3.sqlite3 PUBLIC_ORIGIN=http://127.0.0.1:5001 PORT=5001 .venv/bin/python server.py
    # Outro terminal:
    npx playwright install chromium
    npx playwright test

Pode usar CHROME_PATH=/usr/bin/google-chrome. O teste gera a fixture de foto.

## Migração e limites

flask --app server init-db cria tabelas adicionais sem remover contas/guias. scripts/migrate_sqlite.py importa o MVP antigo em PostgreSQL vazio e mantém a origem.

Docker/HTTPS não foram executados nesta máquina. DNS e chamadas reais a Stripe, SMTP, Google, Kimi, Geoapify e S3 precisam de validação com as contas do operador. Os controles e modelos documentais não certificam conformidade legal. Backup externo e alertas só funcionam quando configurados.
