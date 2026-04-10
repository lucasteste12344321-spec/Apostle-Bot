# Discord clan bot

Bot de Discord em Python para:

- salvar mensagens, edicoes e delecoes em SQLite
- registrar quem entrou e quem saiu do servidor
- rastrear convites criados e quem entrou por cada convite
- receber reports com ticket privado, prova e transcript
- criar painel persistente para membros escolherem se podem ajudar
- abrir painel de tickets para suporte, recrutamento, parceria e denuncia
- abrir ticket de teste de grade com avaliador assumindo, notas e atribuicao de grade por botoes
- abrir ticket de desafio de grade com arbitragem, liberacao do server, resultado e dodge
- aplicar warn, timeout, kick, ban e blacklist
- registrar presenca do cla, ranking de ajuda e historicos
- rodar automod basico, anti-raid e dashboard web simples

## Requisitos

- Python 3.10+
- Um bot criado no Discord Developer Portal
- Escopos de convite: `bot` e `applications.commands`

## Permissoes recomendadas do bot

- View Channels
- Send Messages
- Embed Links
- Attach Files
- Read Message History
- Manage Roles
- Manage Server
- Manage Channels
- Manage Messages
- View Audit Log
- Moderate Members
- Kick Members
- Ban Members

## Intents que precisam estar ligados no portal

- Server Members Intent
- Message Content Intent

Sem esses intents, o bot nao consegue salvar conteudo de mensagem nem rastrear entradas e saidas corretamente.

## Como rodar

1. Copie `.env.example` para `.env`.
2. Preencha pelo menos `DISCORD_TOKEN`.
3. Opcionalmente preencha `DEV_GUILD_ID` para sincronizar os slash commands instantaneamente em um servidor de teste.
4. Instale as dependencias:

```bash
py -m pip install -r requirements.txt
```

5. Inicie o bot:

```bash
py main.py
```

## Deploy no Railway

O projeto ja esta preparado para Railway com [.python-version](C:/Users/SPXBR33317/Desktop/bot%20Discor/.python-version) e [railway.json](C:/Users/SPXBR33317/Desktop/bot%20Discor/railway.json).

Passo a passo:

1. Suba esse projeto para um repositorio no GitHub.
2. No Railway, crie um projeto novo e escolha `Deploy from GitHub repo`.
3. Selecione este repositorio e deixe o Railway detectar o projeto Python.
4. Em `Variables`, adicione pelo menos:
   - `DISCORD_TOKEN`
   - `DATABASE_PATH=/data/bot.sqlite3`
   - `BOT_LOG_PATH=/data/bot.log`
   - opcionalmente `DATA_DIR=/data`, `DEV_GUILD_ID`, `LOG_CHANNEL_ID`, `REPORT_CHANNEL_ID`, `HELP_CHANNEL_ID`, `CLAN_MEMBER_ROLE_ID`, `EVALUATOR_ROLE_ID`, `REFEREE_ROLE_ID`, `REFEREE_ROLE_NAME`, `GRADE_ROLE_IDS`, `GRADE_ROLE_LABELS`, `GRADE_SUBTIER_ROLE_IDS`, `GRADE_SUBTIER_LABELS`, `DASHBOARD_PORT` e `DASHBOARD_TOKEN`
5. Em `Volumes`, crie um volume e monte em `/data`.
6. Faca o primeiro deploy.

Observacoes importantes para Railway:

- O bot pode rodar como worker em background.
- Se quiser usar o dashboard web, exponha a porta do `DASHBOARD_PORT`.
- Sem volume, o arquivo SQLite nao persiste entre reinicios e deploys.
- Se voce alterar variaveis, faca um redeploy para garantir que o processo reinicie com os novos valores.

## Configuracao inicial dentro do Discord

Depois que o bot entrar no servidor:

1. Use `/configurar_canais` para definir os canais de logs, reports e ajuda.
2. Use `/painel_ajuda` para criar o painel de status de ajuda.
3. Use `/painel_tickets` para criar o painel de atendimento e denuncias.
4. Use `/painel_grades` para criar o painel competitivo de grades.
5. Se quiser usar cargos ja existentes, use `/configurar_cargos_ajuda`.
6. Opcionalmente use `/configurar_notificacao_ajuda` e `/configurar_seguranca`.

Depois que o painel de ajuda for criado uma vez na versao nova, o bot guarda a mensagem e reanexa os botoes apos reinicios e redeploys.

No `/painel_tickets`, os botoes atuais incluem:

- `Suporte`
- `Recrutamento`
- `Parceria`
- `Denuncia`

No `/painel_grades`, os botoes atuais incluem:

- `Pedir teste`
- `Desafio de grade`

## Comandos

- `/configurar_canais`
- `/configurar_cargos_ajuda`
- `/configurar_notificacao_ajuda`
- `/configurar_seguranca`
- `/painel_ajuda`
- `/painel_tickets`
- `/painel_grades`
- `/pedir_ajuda`
- `/reportar`
- `/warn`
- `/timeout`
- `/kickar`
- `/banir`
- `/blacklist_add`
- `/blacklist_remove`
- `/blacklist_lista`
- `/presenca`
- `/presencas`
- `/historico_membro`
- `/historico_reports`
- `/historico_convites`
- `/mensagem_apagada`
- `/ranking_ajuda`
- `/top_grades`
- `/exportar_dados`

## Sistema de grade

O painel de grades agora inclui dois fluxos competitivos:

- `Pedir teste`
  - verifica se o membro tem o cargo configurado em `CLAN_MEMBER_ROLE_ID`
  - abre ticket privado
  - avaliadores ou admins assumem o ticket
  - avaliador registra notas
  - avaliador escolhe a grade final e o nivel `low/mid/high` por botoes
  - o bot aplica os cargos e publica a avaliacao final no ticket
  - se nao houver avaliador online no momento, o horario fica registrado no ticket
  - se o ticket nao for concluido em 1 hora, ele expira sem gerar cooldown de 7 dias

- `Desafio de grade`
  - desafiante informa quem quer desafiar
  - o bot verifica se o alvo esta exatamente uma grade acima
  - abre ticket privado para desafiante, desafiado e staff
  - arbitro ou admin assume a arbitragem
  - arbitro libera o server
  - arbitro registra vencedor ou dodge
  - se o desafiante vencer, o bot troca as grades
  - com 3 dodges, o desafiado desce uma grade

Observacoes:

- A ordem das grades segue `GRADE_ROLE_IDS`.
- Os nomes exibidos nos botoes seguem `GRADE_ROLE_LABELS`.
- Os subtieres `low/mid/high` seguem `GRADE_SUBTIER_ROLE_IDS` ou nomes em `GRADE_SUBTIER_LABELS`.
- Se o cargo de arbitro tiver acento, o ideal e definir `REFEREE_ROLE_ID`.
- Para saber quem esta `online/offline` de verdade entre os avaliadores, o bot precisaria do `Presence Intent`. Sem isso, ele registra a demanda sem afirmar quem estava offline.

## Onde os dados ficam salvos

O banco SQLite fica em `data/bot.sqlite3` por padrao.

Tabelas principais:

- `messages`
- `message_edits`
- `member_events`
- `invite_events`
- `reports`
- `help_requests`
- `tickets`
- `ticket_events`
- `grade_profiles`
- `grade_assessments`
- `grade_challenges`
- `moderation_actions`
- `blacklist_entries`
- `presence_status`
- `automod_events`

## Observacoes importantes

- O bot so consegue salvar o conteudo de mensagens que ele viu enquanto estava online.
- Para descobrir quem apagou mensagem de outra pessoa, o bot precisa da permissao `View Audit Log`.
- Para rastrear convites, o bot precisa da permissao `Manage Server`.
- Se o bot nao tiver `Manage Roles`, ele nao consegue criar ou trocar os cargos do sistema de ajuda.
- Para abrir e fechar tickets de report pelo botao, o bot precisa de `Manage Channels`.
- Para automod com timeout automatico, o bot precisa de `Moderate Members`.
- Se voce expor o dashboard web publicamente, use `DASHBOARD_TOKEN`.
- Se um token do bot for exposto em arquivo, log ou commit, gere um novo token no Discord Developer Portal imediatamente.
