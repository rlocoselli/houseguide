# Registro operacional de tratamento — versão de trabalho

Responsável informado: **Audela de donnees**, França (a confirmar documentalmente).
Contato: **admin@audeladedonnees.fr**. Domínio: **houseguide.audeladedonnees.fr**.

Faltam endereço, registro da entidade e validação dos documentos. O site sinaliza a situação.

| Tratamento | Dados | Finalidade/base a avaliar | Retenção |
| --- | --- | --- | --- |
| Conta | nome, e-mail, hash ou subject Google | contrato e segurança | até exclusão |
| Aceite | versão e instante, sem IP | evidência contratual | até exclusão |
| Guia/fotos | conteúdo do anfitrião, fotos sem EXIF | serviço; anfitrião controlador do conteúdo pessoal | até exclusão; órfãs após 24h |
| IA | campos solicitados | funcionalidade contratada/instrução | texto final no guia; avaliar retenção Moonshot |
| Pagamento | IDs e estado Stripe | contrato/obrigações fiscais | até exclusão local; retenção externa a avaliar |
| E-mail | destinatário e link | autenticação/segurança | token 1h ou 24h; SMTP a avaliar |
| Estatísticas | guia, dia, contagem | operação sem identificar visitante | 90 dias |
| Limites | HMAC, janela, contagem | segurança/interesse legítimo | 48h |
| Direitos | tipo, mensagem, estado | exercício de direitos | até exclusão; avaliar evidência caso a caso |
| Backups | banco e fotos | continuidade | 30 dias por padrão |
| Exclusões | ID interno e instante | impedir restauração indevida | retenção de backup (mínimo30d) + 7d |

## Fornecedores a avaliar antes de ativar

- Hospedagem/PostgreSQL: prestador, região, acesso administrativo e contrato.
- SMTP: prestador, região, retenção e contrato.
- Stripe: conta comercial, preço, impostos, contrato e transferências.
- Google: projeto OAuth, política/contrato e transferências.
- Moonshot/Kimi: região, retenção de prompts, treinamento, contrato e salvaguardas.
- Geoapify/OSM: termos, atribuição, contrato e transferências.
- S3 opcional: região, bucket privado, ciclo de vida e contrato.

Não afirmar residência exclusiva na UE sem verificar toda a cadeia. Evite enviar dados de hóspedes ou categorias sensíveis para IA. O anfitrião deve ter base legal para informações pessoais em um guia público. Acesso, exportação e exclusão não substituem atendimento humano.

## Documentos públicos

/legal oferece termos, privacidade, cookies/offline e acordo de tratamento em PT/IT/EN/DE/FR/ES. Revise conforme o modelo comercial real B2B/B2C, país da entidade, direitos de retratação, mediação de consumo quando aplicável e contratos dos provedores. Os documentos não afastam direitos obrigatórios.

Fontes: [Comissão Europeia](https://commission.europa.eu/law/law-topic/data-protection/information-individuals_en), [pedidos de titulares](https://commission.europa.eu/law/law-topic/data-protection/information-business-and-organisations/dealing-requests-individuals_en), [CNIL](https://www.cnil.fr/) e [ANPD](https://www.gov.br/anpd/pt-br/assuntos/titular-de-dados-1/direito-dos-titulares).
