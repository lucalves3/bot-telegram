"""Price Hunter: monitora preços e avisa no Telegram quando baixam.

Uso:
    python main.py run      # bot (comandos) + checagem periódica
    python main.py check    # uma checagem e sai (para o Agendador de Tarefas)
"""
import html
import os
import sys
import time
from urllib.parse import urlsplit

from dotenv import load_dotenv

import db
import scraper
import telegram

load_dotenv()


def brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def per_unit(price, units):
    return f"{brl(price / units)}/un" if price and units else None


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
        pu = per_unit(price, item["units"])
        if pu:
            lines[-1] += f" ({pu})"
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


MAX_HITS = 15


def check_watch(w):
    """Lê a listagem e devolve os produtos novos (ou mais baratos) abaixo do limite por unidade."""
    seen = db.get_seen(w["id"])
    hits, expensive = [], []
    for p in scraper.fetch_listing(w["url"]):
        title = p["title"].lower()
        if not p["units"] or any(x in title for x in scraper.EXCLUDE_WORDS):
            continue
        unit = p["price"] / p["units"]
        if unit >= w["max_unit"]:
            if p["asin"] in seen:
                expensive.append(p["asin"])  # voltou a ficar caro: rearma o aviso
            continue
        last = seen.get(p["asin"])
        if last is None or unit < last * 0.95:
            hits.append({**p, "unit": unit})
    db.forget_seen(w["id"], expensive)
    hits.sort(key=lambda h: h["unit"])
    for h in hits:
        db.set_seen(w["id"], h["asin"], h["unit"])
    return hits


def send_hits(cid, w, hits):
    if not hits:
        return
    host = urlsplit(w["url"]).netloc
    shown = hits[:MAX_HITS]
    head = f"🛒 <b>{html.escape(w['name'])}</b>: {len(hits)} abaixo de {brl(w['max_unit'])}/un"
    blocks = [
        f"{n}. <b>{html.escape(h['title'][:90])}</b>\n"
        f"{brl(h['price'])} · {h['units']} un · <b>{brl(h['unit'])}/un</b>\n"
        f"https://{host}/dp/{h['asin']}"
        for n, h in enumerate(shown, 1)
    ]
    if len(hits) > len(shown):
        blocks.append(f"+ {len(hits) - len(shown)} outros")
    msg = head
    for b in blocks:
        if len(msg) + len(b) > 3800:
            telegram.send(cid, msg, preview=False)
            msg = ""
        msg += ("\n\n" if msg else "") + b
    telegram.send(cid, msg, preview=False)


def check_all():
    for item in db.list_items():
        try:
            p = check_item(item)
            print(f"[ok] {item['name']}: {brl(p)}")
        except Exception as e:
            print(f"[erro] {item['name']}: {e}")
        time.sleep(2)
    for w in db.list_watches():
        try:
            hits = check_watch(w)
            print(f"[ok] {w['name']}: {len(hits)} novos")
            cid = chat_id()
            if cid:
                send_hits(cid, w, hits)
        except Exception as e:
            print(f"[erro] {w['name']}: {e}")


HELP = (
    "<b>Price Hunter</b>\n"
    "/add &lt;url&gt; [preço-alvo] [seletor CSS] - monitorar item\n"
    "/lista - itens e últimos preços\n"
    "/remover &lt;id&gt; - parar de monitorar\n"
    "/unidades &lt;id&gt; &lt;qtd&gt; - corrigir a quantidade do pacote (preço por unidade)\n"
    "/categoria &lt;link da busca Amazon&gt; [máx R$/un] | Nome - avisa de fraldas abaixo do preço por unidade (padrão R$ 1,00)\n"
    "/categorias - buscas monitoradas\n"
    "/remover_cat &lt;id&gt; - parar uma busca\n"
    "/checar - checar agora\n"
    "Nome do item: coloque depois do link com |, ex.: /add url 1500 | Meu item"
)


def admin_ids():
    return {a.strip() for a in os.getenv("ADMIN_IDS", "").split(",") if a.strip()}


def is_admin(uid):
    admins = admin_ids()
    return not admins or str(uid) in admins


def handle(text, cid, uid=None):
    parts = text.strip().split()
    cmd = parts[0].split("@")[0].lower()

    if not is_admin(uid):
        return  # só os usuários em ADMIN_IDS podem dar comandos

    allowed = chat_id()
    # no privado (chat id == id do usuário) o admin pode configurar; as respostas
    # ficam lá e só os avisos de preço vão pro grupo
    in_dm = bool(admin_ids()) and str(cid) == str(uid)
    if allowed and str(cid) != str(allowed) and not in_dm:
        return  # só o chat configurado (ou o privado do admin) pode dar comandos
    if not allowed:
        if cmd != "/start":
            return  # nenhum chat configurado ainda: só /start é aceito
        db.set_setting("chat_id", cid)
        telegram.send(cid, "Chat de avisos definido.")

    if cmd in ("/start", "/help"):
        telegram.send(cid, HELP)
    elif cmd == "/add":
        body = text.split(None, 1)[1] if len(parts) > 1 else ""
        main_part, _, name = body.partition("|")
        args = main_part.split()
        if not args:
            return telegram.send(cid, "Uso: /add &lt;url&gt; [preço-alvo] [seletor]")
        url = scraper.normalize_url(args[0])
        target = scraper.parse_price(args[1]) if len(args) > 1 else None
        selector = " ".join(args[2:]) or None
        try:
            info = scraper.fetch_info(url, selector)
        except Exception as e:
            return telegram.send(cid, f"Não consegui ler o preço: {html.escape(str(e))}")
        price, title = info["price"], info["title"]
        name = name.strip() or title[:80] or url[:50]
        units = scraper.parse_units(title)
        try:
            db.add_item(name, url, target, selector, units)
        except Exception:
            return telegram.send(cid, "Esse link já está sendo monitorado.")
        item = [i for i in db.list_items() if i["url"] == url][0]
        db.record_price(item["id"], price)
        msg = f"✅ Monitorando <b>{html.escape(name)}</b> (id {item['id']})\nPreço atual: {brl(price)}"
        if units:
            msg += f"\n{units} unidades: {per_unit(price, units)}"
        telegram.send(cid, f"{msg}\n{url}")
    elif cmd == "/lista":
        items = db.list_items()
        if not items:
            return telegram.send(cid, "Nenhum item. Use /add.")
        out = []
        for i in items:
            now = brl(i["last_price"]) if i["last_price"] else "-"
            pu = per_unit(i["last_price"], i["units"])
            now += f" ({pu})" if pu else ""
            alvo = f" (alvo {brl(i['target'])})" if i["target"] else ""
            out.append(f"{i['id']}. <b>{html.escape(i['name'])}</b>: {now}{alvo}")
        telegram.send(cid, "\n".join(out))
    elif cmd == "/remover":
        ok = len(parts) > 1 and parts[1].isdigit() and db.remove_item(int(parts[1]))
        telegram.send(cid, "Removido." if ok else "Id não encontrado. Veja /lista.")
    elif cmd == "/categoria":
        body = text.split(None, 1)[1] if len(parts) > 1 else ""
        main_part, _, name = body.partition("|")
        args = main_part.split()
        if not args:
            return telegram.send(cid, "Uso: /categoria &lt;link da busca&gt; [máx R$/un] | Nome")
        url = scraper.normalize_url(args[0])
        if not scraper.is_amazon(url):
            return telegram.send(cid, "Por enquanto só links de busca da Amazon.")
        max_unit = (scraper.parse_price(args[1]) if len(args) > 1 else None) or 1.0
        name = name.strip() or "Fraldas"
        try:
            w = db.add_watch(name, url, max_unit)
        except Exception:
            return telegram.send(cid, "Essa busca já está sendo monitorada.")
        telegram.send(cid, f"✅ Monitorando <b>{html.escape(name)}</b> (id {w['id']}), abaixo de {brl(max_unit)}/un. Lendo...")
        try:
            hits = check_watch(w)
        except Exception as e:
            db.remove_watch(w["id"])
            return telegram.send(cid, f"Não consegui ler a listagem: {html.escape(str(e))}")
        if hits:
            send_hits(cid, w, hits)
        else:
            telegram.send(cid, "Nada abaixo do limite agora. Aviso quando aparecer.")
    elif cmd == "/categorias":
        ws = db.list_watches()
        if not ws:
            return telegram.send(cid, "Nenhuma busca. Use /categoria.")
        telegram.send(
            cid,
            "\n".join(f"{w['id']}. <b>{html.escape(w['name'])}</b>: abaixo de {brl(w['max_unit'])}/un" for w in ws),
        )
    elif cmd == "/remover_cat":
        ok = len(parts) > 1 and parts[1].isdigit() and db.remove_watch(int(parts[1]))
        telegram.send(cid, "Removida." if ok else "Id não encontrado. Veja /categorias.")
    elif cmd == "/unidades":
        ok = (
            len(parts) > 2
            and parts[1].isdigit()
            and parts[2].isdigit()
            and int(parts[2]) > 0
            and db.set_units(int(parts[1]), int(parts[2]))
        )
        telegram.send(cid, "Unidades atualizadas." if ok else "Uso: /unidades &lt;id&gt; &lt;quantidade&gt;")
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
                    handle(text, msg["chat"]["id"], (msg.get("from") or {}).get("id"))
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
