# Discord clan bot

Bot de Discord em Python para:

- salvar mensagens, edicoes e delecoes em SQLite
- registrar quem entrou e quem saiu do servidor
- rastrear convites criados e quem entrou por cada convite
- receber reports com embed pronto e prova em anexo
- criar painel para membros escolherem se podem ajudar
- marcar membros disponiveis quando alguem usar `/pedir_ajuda`

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
- View Audit Log

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
   - opcionalmente `DEV_GUILD_ID`, `LOG_CHANNEL_ID`, `REPORT_CHANNEL_ID` e `HELP_CHANNEL_ID`
5. Em `Volumes`, crie um volume e monte em `/data`.
6. Faça o primeiro deploy.

Observacoes importantes para Railway:

- Esse bot nao precisa de dominio publico nem porta HTTP. Ele roda como worker em background.
- Sem volume, o arquivo SQLite nao persiste entre reinicios e deploys.
- Se voce alterar variaveis, faca um redeploy para garantir que o processo reinicie com os novos valores.
- Para mais estabilidade, um plano pago costuma ser mais seguro para bot 24/7 do que depender apenas do free.

## Configuracao inicial dentro do Discord

Depois que o bot entrar no servidor:

1. Use `/configurar_canais` para definir os canais de logs, reports e ajuda.
2. Use `/painel_ajuda` para criar o painel de status de ajuda.
3. Se quiser usar cargos ja existentes, use `/configurar_cargos_ajuda`.

## Comandos

- `/configurar_canais`
- `/configurar_cargos_ajuda`
- `/painel_ajuda`
- `/pedir_ajuda`
- `/reportar`

## Onde os dados ficam salvos

O banco SQLite fica em `data/bot.sqlite3` por padrao.

Tabelas principais:

- `messages`
- `message_edits`
- `member_events`
- `invite_events`
- `reports`
- `help_requests`

## Observacoes importantes

- O bot so consegue salvar o conteudo de mensagens que ele viu enquanto estava online.
- Para descobrir quem apagou mensagem de outra pessoa, o bot precisa da permissao `View Audit Log`.
- Para rastrear convites, o bot precisa da permissao `Manage Server`.
- Se o bot nao tiver `Manage Roles`, ele nao consegue criar ou trocar os cargos do sistema de ajuda.
- Se um token do bot for exposto em arquivo, log ou commit, gere um novo token no Discord Developer Portal imediatamente.
