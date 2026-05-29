# Chatbot de agendamento

Projeto local para agendar horarios pelo site e pelo WhatsApp, salvando tudo em uma planilha CSV que pode ser aberta no Excel, Google Sheets ou LibreOffice.

## O que ja vem pronto

- Pagina de agendamento em `http://127.0.0.1:8000/`
- Painel de registros em `http://127.0.0.1:8000/admin`
- Planilha CSV em `data/agendamentos.csv`
- Webhook de WhatsApp em `/webhook/whatsapp`
- Compatibilidade com Twilio WhatsApp Sandbox e payload basico da WhatsApp Cloud API
- Fluxo inteligente de conversa: servico, data, horario e nome
- Painel admin com senha
- Cancelamento, remarcacao e lembrete manual no painel
- Confirmacao automatica no WhatsApp para agendamentos feitos pelo site, quando Twilio ou WhatsApp Cloud API estiver configurado
- Bloqueio de almoco, datas indisponiveis, antecedencia minima e duracao por servico
- Opcao de polir respostas com OpenAI via `OPENAI_API_KEY`

## Como rodar

```powershell
python app.py
```

Depois abra:

```text
http://127.0.0.1:8000/
```

Painel admin:

```text
http://127.0.0.1:8000/admin
```

Senha padrao local:

```text
admin123
```

Troque em producao usando `ADMIN_PASSWORD` e `ADMIN_SECRET`.

## Subir para o GitHub

Na primeira vez:

```powershell
git init
git add .
git commit -m "Initial scheduling chatbot"
git branch -M main
git remote add origin https://github.com/SEU-USUARIO/SEU-REPOSITORIO.git
git push -u origin main
```

Antes de publicar, confirme que arquivos privados nao entraram no commit. O projeto ignora `.env`, `config.json`, `.vercel/` e os arquivos gerados em `data/`.

## Como subir online

O projeto ja inclui configuracao para Vercel e tambem para plataformas Python/PaaS que rodam comando web.

### Opcao Vercel

O projeto inclui:

```text
vercel.json
api/index.py
requirements.txt
```

Na Vercel, o backend roda como Python Function, e as paginas estaticas ficam em `static/`.

1. Crie um repositorio no GitHub e envie esta pasta.
2. Na Vercel, clique em Add New > Project.
3. Importe o repositorio.
4. Deixe Framework Preset como Other.
5. Deploy.

Depois do deploy, a pagina publica ficara em uma URL parecida com:

```text
https://seu-app.vercel.app/
```

Use essa mesma URL para o webhook:

```text
https://seu-app.vercel.app/webhook/whatsapp
```

Variaveis opcionais na Vercel:

```text
WHATSAPP_VERIFY_TOKEN=agenda-bot
ADMIN_PASSWORD=uma-senha-forte
ADMIN_SECRET=um-segredo-longo-aleatorio
OPENAI_API_KEY=sua-chave-opcional
OPENAI_POLISH_WHATSAPP=false
```

Importante: em Vercel, arquivos gravados pela Function nao sao uma planilha persistente de producao. O app usa `/tmp` para nao quebrar o demo, mas os agendamentos podem sumir quando a Function reinicia. Para uso real em Vercel, conecte a persistencia com Google Sheets API, Supabase, Neon, Vercel KV/Blob ou outro armazenamento externo.

### Opcao rapida: Render

1. Crie um repositorio no GitHub e envie esta pasta.
2. No Render, crie um novo Web Service a partir desse repositorio.
3. Use:

```text
Build command: pip install -r requirements.txt
Start command: HOST=0.0.0.0 python app.py
```

4. Cadastre as variaveis de ambiente que quiser usar:

```text
WHATSAPP_VERIFY_TOKEN=agenda-bot
OPENAI_API_KEY=sua-chave-opcional
OPENAI_POLISH_WHATSAPP=false
```

5. Depois do deploy, a pagina publica ficara em uma URL parecida com:

```text
https://seu-app.onrender.com/
```

Use essa mesma URL para o webhook:

```text
https://seu-app.onrender.com/webhook/whatsapp
```

### Opcao via Railway/Fly/Heroku-like

Use o `Procfile`:

```text
web: HOST=0.0.0.0 python app.py
```

A plataforma precisa definir a variavel `PORT`. O app le essa porta automaticamente.

### Importante sobre a planilha

Em hospedagem gratuita, serverless ou sem disco persistente, arquivos CSV locais podem ser resetados quando o servidor reinicia. Para uso real, o proximo passo ideal e trocar `data/agendamentos.csv` por Google Sheets API ou por um banco simples com exportacao para planilha.

## Configurar servicos e horarios

Copie `config.example.json` para `config.json` e edite:

```powershell
Copy-Item config.example.json config.json
```

Campos principais:

- `business_name`: nome mostrado no site
- `slot_minutes`: tamanho de cada horario
- `min_notice_hours`: antecedencia minima para agendar
- `max_days_ahead`: limite maximo de dias no futuro
- `services`: lista de servicos, com duracao individual
- `breaks`: intervalos bloqueados, como almoco
- `unavailable_dates`: datas fechadas no formato `YYYY-MM-DD`
- `hours`: horario de funcionamento por dia da semana

## Planilha

Os agendamentos entram em:

```text
data/agendamentos.csv
```

Esse arquivo e a planilha local do projeto. Para usar com Google Sheets, voce pode importar o CSV ou sincronizar esse arquivo com uma automacao depois. A estrutura atual das colunas e:

```text
id, created_at, name, phone, service, date, time, notes, source, status
```

## WhatsApp com Twilio

1. Rode o app localmente.
2. Exponha a porta com ngrok, Cloudflare Tunnel ou similar.
3. No Twilio WhatsApp Sandbox, configure o webhook de mensagens para:

```text
https://SEU-DOMINIO/webhook/whatsapp
```

4. Metodo: `POST`.

O app responde em TwiML quando recebe campos `From` e `Body`, que e o formato padrao do Twilio.

Para enviar confirmacoes automaticas e lembretes manuais pelo painel, configure:

```text
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
```

## WhatsApp Cloud API

Para verificar o webhook, configure o token:

```powershell
$env:WHATSAPP_VERIFY_TOKEN="agenda-bot"
$env:WHATSAPP_ACCESS_TOKEN="token-da-meta"
$env:WHATSAPP_PHONE_NUMBER_ID="id-do-numero"
python app.py
```

Use a URL:

```text
https://SEU-DOMINIO/webhook/whatsapp
```

O endpoint aceita o payload de entrada da Cloud API, processa a conversa e envia a resposta de volta pelo endpoint `/{PHONE_NUMBER_ID}/messages` da Graph API.

Para enviar confirmacoes automaticas e lembretes manuais pelo painel usando a Cloud API, configure:

```text
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_GRAPH_VERSION=v25.0
```

Na Meta, use:

```text
Callback URL: https://SEU-DOMINIO/webhook/whatsapp
Verify token: mesmo valor de WHATSAPP_VERIFY_TOKEN
Webhook field: messages
```

## IA opcional

O fluxo de agendamento funciona sem chave de IA. Se quiser deixar as respostas do WhatsApp mais naturais, ative:

```powershell
$env:OPENAI_API_KEY="sua-chave"
$env:OPENAI_POLISH_WHATSAPP="true"
python app.py
```

O app usa a Responses API da OpenAI para reescrever a resposta factual sem mudar data, horario ou servico.

## Teste rapido do webhook

Com o servidor rodando:

```powershell
Invoke-WebRequest -UseBasicParsing -Method POST `
  -Uri http://127.0.0.1:8000/webhook/whatsapp `
  -ContentType "application/x-www-form-urlencoded" `
  -Body "From=whatsapp:+5511999999999&Body=menu"
```
