# Price Hunter

Monitora preços de produtos e avisa no Telegram quando baixam.

## Setup

```powershell
cd D:\SaaS\price-hunter
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py run
```

O token do bot fica em `.env` (não vai pro git). Depois de subir, abra
`t.me/pricehunter_br_2026_bot` e mande `/start` para o bot guardar seu chat.

## Comandos no Telegram

| Comando | O que faz |
|---|---|
| `/add <url> [alvo] [seletor CSS] \| Nome` | começa a monitorar |
| `/lista` | itens e último preço |
| `/remover <id>` | para de monitorar |
| `/checar` | força uma checagem agora |

Exemplo: `/add https://loja.com/produto 1500 | Meu item`

## Regra de aviso

- **Com preço-alvo**: avisa quando o preço fica ≤ alvo (e de novo só se cair mais).
- **Sem alvo**: avisa a cada queda em relação à última checagem.

## Como o preço é lido

Automático via JSON-LD / meta tags (`product:price:amount`, `itemprop=price`).
Se a loja não expõe isso, passe um seletor CSS no `/add`. Lojas que carregam o
preço só por JavaScript ou bloqueiam bots vão precisar de Playwright (ainda não incluso).

## Deploy no Railway

1. Novo projeto a partir do repositório do GitHub (serviço worker, sem porta).
2. Adicione um **Volume** montado em `/data`.
3. Em Variables:
   - `TELEGRAM_TOKEN` = token do BotFather
   - `DB_PATH` = `/data/pricehunter.db`
   - `CHECK_INTERVAL_MIN` = `60`
   - `TELEGRAM_CHAT_ID` = ID do grupo (ex.: `-5387764795`)
   - `ADMIN_IDS` = seu ID de usuário (pegue com @userinfobot); vários separados por vírgula
4. Start command: `python main.py run` (já está no `Procfile`).
5. Mande `/start` pro bot para ele guardar o chat.

## Rodando sempre

- `python main.py run`: bot + checagem a cada `CHECK_INTERVAL_MIN` (padrão 60 min).
- `python main.py check`: uma checagem só, para o Agendador de Tarefas do Windows.

## Configurar pelo privado

Com `ADMIN_IDS` definido, o admin pode mandar os comandos direto no privado do bot.
As respostas ficam no privado e só os avisos de preço vão pro grupo (`TELEGRAM_CHAT_ID`).

## Links

O bot limpa o link antes de salvar: remove parâmetros de rastreamento (`utm_*`, `gclid`, `fbclid`...)
e, na Amazon, reduz para `https://www.amazon.com.br/dp/<ASIN>`. Para a Amazon o seletor do preço
já é padrão, então basta `/add <link> [alvo] | Nome`.

## Busca por categoria (Amazon)

`/categoria <link de busca da Amazon> [máx R$/un] | Nome` lê as 2 primeiras páginas da busca,
calcula o preço por unidade pelo título e avisa dos produtos abaixo do limite (padrão R$ 1,00).
Só avisa de produtos novos ou que baixaram 5%+. Ignora geriátricas/adulto e títulos sem quantidade.
Mercado Livre bloqueia leitura automática (verificação de conta / API 403), então não é suportado.
