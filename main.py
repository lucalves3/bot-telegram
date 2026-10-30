"""Price Hunter: monitora preços e avisa no Telegram quando baixam.

Uso:
    python main.py run      # bot (comandos) + checagem periódica
    python main.py check    # uma checagem e sai (para o Agendador de Tarefas)
"""
import html
import os
import sys
import time

from dotenv import load_dotenv

import db
import scraper
import telegram

load_dotenv()


def brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def chat_id():
    return os.getenv("TELEGRAM_CHAT_ID") or db.get_setting("chat_id")


def check_item(item):
    price = scraper.fetch_price(item["url"], item["selector"])
    prev = item["last_price"]
    db.record_price(item["id"], price)

    target, last_alert = item["target"], item["last_alert_price"]
    below_target = target is None or price <= target
    dropped = prev is not None and price < prev
    # com alvo: avisa ao entrar na faixa; sem alvo: avisa qualquer queda
    should_alert = below_target and (dropped if target is None else True)
    if should_alert and (last_alert is None or price < last_alert):
        lines = [f"📉 <b>{html.escape(item['name'])}</b>", f"Agora: <b>{brl(price)}</b>"]
        if prev is not None and prev != price:
            lines.append(f"Antes: {brl(prev)}")
        if target is not None:
            lines.append(f"Seu alvo: {brl(target)}")
        lines.append(item["url"])
        cid = chat_id()
        if cid:
            telegram.send(cid, "\n".join(lines))
        else:
            print("Sem chat_id: mande /start pro bot.")
        db.set_alert_price(item["id"], price)
    elif target is not None and price > target:
        db.set_alert_price(item["id"], None)  # saiu da faixa, rearma o aviso
    return price


def check_all():
    for item in db.list_items():
        try:
            p = check_item(item)
            print(f"[ok] {item['name']}: {brl(p)}")
        except Exception as e:
            print(f"[erro] {item['name']}: {e}")
        time.sleep(2)


HELP = (
    "<b>Price Hunter</b>\n"
    "/add &lt;url&gt; [preço-alvo] [seletor CSS] - monitorar item\n"
    "/lista - itens e últimos preços\n"
    "/remover &lt;id&gt; - parar de monitorar\n"
    "/checar - checar agora\n"
    "Nome do item: coloque depois do link com |, ex.: /add url 1500 | Meu item"
)


def handle(text, cid):
    parts = text.strip().split()
    cmd = parts[0].split("@")[0].lower()

    allowed = chat_id()
    if allowed and str(cid) != str(allowed):
        return  # só o chat configurado pode dar comandos
    if not allowed:
        if cmd != "/start":
            return  # nenhum chat configurado ainda: só /start é aceito
        db.set_setting("chat_id", cid)

    if cmd in ("/start", "/help"):
        telegram.send(cid, HELP)
    elif cmd == "/add":
        body = text.split(None, 1)[1] if len(parts) > 1 else ""
        main_part, _, name = body.partition("|")
        args = main_part.split()
        if not args:
            return telegram.send(cid, "Uso: /add &lt;url&gt; [preço-alvo] [seletor]")
        url = args[0]
        target = scraper.parse_price(args[1]) if len(args) > 1 else None
        selector = " ".join(args[2:]) or None
        name = name.strip() or url[:50]
        try:
            price = scraper.fetch_price(url, selector)
        except Exception as e:
            return telegram.send(cid, f"Não consegui ler o preço: {html.escape(str(e))}")
        try:
            db.add_item(name, url, target, selector)
        except Exception:
            return telegram.send(cid, "Esse link já está sendo monitorado.")
        item = [i for i in db.list_items() if i["url"] == url][0]
        db.record_price(item["id"], price)
        telegram.send(cid, f"✅ Monitorando <b>{html.escape(name)}</b>\nPreço atual: {brl(price)}")
    elif cmd == "/lista":
        items = db.list_items()
        if not items:
            return telegram.send(cid, "Nenhum item. Use /add.")
        out = []
        for i in items:
            now = brl(i["last_price"]) if i["last_price"] else "-"
            alvo = f" (alvo {brl(i['target'])})" if i["target"] else ""
            out.append(f"{i['id']}. <b>{html.escape(i['name'])}</b>: {now}{alvo}")
        telegram.send(cid, "\n".join(out))
    elif cmd == "/remover":
        ok = len(parts) > 1 and parts[1].isdigit() and db.remove_item(int(parts[1]))
        telegram.send(cid, "Removido." if ok else "Id não encontrado. Veja /lista.")
    elif cmd == "/checar":
        telegram.send(cid, "Checando...")
        check_all()
        telegram.send(cid, "Pronto.")


def run():
    interval = int(os.getenv("CHECK_INTERVAL_MIN", "60")) * 60
    next_check = 0
    offset = None
    print("Bot rodando. Ctrl+C para sair.")
    while True:
        if time.time() >= next_check:
            check_all()
            next_check = time.time() + interval
        try:
            for u in telegram.get_updates(offset):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                text = msg.get("text", "")
                if text.startswith("/"):
                    handle(text, msg["chat"]["id"])
        except Exception as e:
            print(f"[erro bot] {e}")
            time.sleep(5)


if __name__ == "__main__":
    db.init()
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "check":
        check_all()
    else:
        run()
