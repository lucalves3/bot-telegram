"""Price Hunter: monitora preços e avisa no Telegram quando baixam.

Uso:
    python main.py run      # bot (comandos) + checagem periódica
    python main.py check    # uma checagem e sai (para o Agendador de Tarefas)
"""
import html
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from dotenv import load_dotenv

import affiliate
import db
import messages
import scraper
import telegram
import themes
import whatsapp
from messages import brl

load_dotenv()

BRT = timezone(timedelta(hours=-3))  # horário de Brasília (sem horário de verão)


def per_unit(price, units):
    return f"{brl(price / units)}/un" if price and units else None


def chat_id():
    return os.getenv("TELEGRAM_CHAT_ID") or db.get_setting("chat_id")


def notify(cid, text, image=None):
    """Envia no Telegram com a foto do produto; se a foto falhar, manda só o texto."""
    if image:
        try:
            return telegram.send_photo(cid, image, text)
        except Exception as e:
            print(f"[foto] {e}")
    return telegram.send(cid, text, preview=not image)


def notify_wa(text, image=None):
    jid = whatsapp.group_id()
    if image:
        try:
            return whatsapp.send(jid, text, image)
        except Exception as e:
            print(f"[whatsapp foto] {e}")
    return whatsapp.send(jid, text)


def has_channel():
    return bool(chat_id()) or bool(whatsapp.group_id())


def broadcast(make_text, image=None):
    """Posta em todos os canais; make_text(canal) monta o texto com o link de afiliado do canal.

    Levanta erro só se nenhum canal configurado conseguiu enviar.
    """
    sent, errors = 0, []
    cid = chat_id()
    if cid:
        try:
            notify(cid, make_text("telegram"), image)
            sent += 1
        except Exception as e:
            errors.append(f"telegram: {e}")
    if whatsapp.group_id():
        try:
            notify_wa(make_text("whatsapp"), image)
            sent += 1
        except Exception as e:
            errors.append(f"whatsapp: {e}")
    for err in errors:
        print(f"[envio] {err}")
    if not sent and errors:
        raise RuntimeError("; ".join(errors))
    return sent


def check_item(item):
    info = scraper.fetch_info(item["url"], item["selector"])
    price = info["price"]
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
        if has_channel():
            broadcast(lambda ch: "\n".join(lines + [affiliate.link(item["url"], ch)]), info["image"])
        else:
            print("Sem canal: mande /start pro bot ou configure WHATSAPP_GROUP_ID.")
        db.set_alert_price(item["id"], price)
    elif target is not None and price > target:
        db.set_alert_price(item["id"], None)  # saiu da faixa, rearma o aviso
    return price


def check_watch(w):
    """Lê a listagem e devolve os produtos novos (ou 5%+ mais baratos) que passam nos alvos da busca.

    Alvos: max_unit (R$/un), max_total (R$ do produto); sem nenhum, aceita qualquer preço.
    """
    seen = db.get_seen(w["id"])
    hits, rearm = [], []
    for p in scraper.fetch_listing(w["url"]):
        if any(x in p["title"].lower() for x in scraper.EXCLUDE_WORDS):
            continue
        unit = p["price"] / p["units"] if p["units"] else None
        ok = True
        if w["max_unit"] is not None:
            ok = unit is not None and unit < w["max_unit"]
        if ok and w["max_total"] is not None:
            ok = p["price"] <= w["max_total"]
        if not ok:
            if p["asin"] in seen:
                rearm.append(p["asin"])  # saiu da faixa: rearma o aviso
            continue
        metric = unit if unit is not None else p["price"]
        last = seen.get(p["asin"])
        if last is None or metric < last * 0.95:
            hits.append({**p, "unit": unit, "metric": metric})
    db.forget_seen(w["id"], rearm)
    hits.sort(key=lambda h: (h["unit"] is None, h["unit"] if h["unit"] is not None else h["price"]))
    return hits  # enqueue_hits marca como visto e coloca na fila de envio


def describe_target(w):
    parts = []
    if w["max_unit"] is not None:
        parts.append(f"abaixo de {brl(w['max_unit'])}/un")
    if w["max_total"] is not None:
        parts.append(f"até {brl(w['max_total'])}")
    return " e ".join(parts) or "qualquer preço"


def enqueue_hits(w, hits):
    """Coloca os achados na fila de envio e marca como vistos (não são reenfileirados)."""
    host = urlsplit(w["url"]).netloc
    seen_at = datetime.now(BRT).strftime("%H:%M")
    for h in hits:
        payload = {
            "title": h["title"],
            "price": h["price"],
            "units": h["units"],
            "size": scraper.parse_size(h["title"], h["units"]),
            "image": h.get("image"),
            "host": host,
            "asin": h["asin"],
            "seen_at": seen_at,
        }
        # mais barato por unidade primeiro; sem quantidade, depois, por preço total
        prio = h["unit"] if h["unit"] is not None else 1e6 + h["price"]
        db.enqueue(w["id"], h["asin"], prio, json.dumps(payload))
        db.set_seen(w["id"], h["asin"], h["metric"])


def send_next():
    """Envia o próximo produto da fila (alterna entre as categorias)."""
    row = db.queue_next() if has_channel() else None
    if not row:
        return
    p = json.loads(row["payload"])
    w = next((x for x in db.list_watches() if x["id"] == row["watch_id"]), None)
    url = f"https://{p['host']}/dp/{p['asin']}"
    cat = w["name"] if w else ""
    try:
        broadcast(lambda ch: messages.product_caption(p, cat, affiliate.link(url, ch)), p.get("image"))
        db.queue_done(row)
    except Exception as e:
        print(f"[fila] {e}")
        db.queue_fail(row)


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
            enqueue_hits(w, hits)
            print(f"[ok] {w['name']}: {len(hits)} novos na fila")
        except Exception as e:
            print(f"[erro] {w['name']}: {e}")


HELP = (
    "<b>Price Hunter</b>\n"
    "/add &lt;url&gt; [preço-alvo] [seletor CSS] - monitorar item\n"
    "/lista - itens e últimos preços\n"
    "/remover &lt;id&gt; - parar de monitorar\n"
    "/unidades &lt;id&gt; &lt;qtd&gt; - corrigir a quantidade do pacote (preço por unidade)\n"
    "/categoria &lt;link da busca Amazon&gt; [alvo] | Nome - avisa dos produtos da busca. "
    "Alvo: 1/un (preço por unidade) e/ou 50 (preço total); sem alvo, avisa de tudo\n"
    "/categorias - buscas monitoradas\n"
    "/fila - produtos esperando para serem postados\n"
    "/remover_cat &lt;id&gt; - parar uma busca\n"
    "/checar - checar agora\n"
    "Nome do item: coloque depois do link com |, ex.: /add url 1500 | Meu item"
    + themes.HELP
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
    # grupos de tema: aceitos se já ligados, ou para o /tema de um admin (exige ADMIN_IDS)
    theme_chat = db.theme_of_chat("telegram", cid) is not None or (cmd == "/tema" and bool(admin_ids()))
    if allowed and str(cid) != str(allowed) and not in_dm and not theme_chat:
        return  # só o chat configurado (ou o privado do admin) pode dar comandos
    if not allowed and not theme_chat:
        if cmd != "/start":
            return  # nenhum chat configurado ainda: só /start é aceito
        db.set_setting("chat_id", cid)
        telegram.send(cid, "Chat de avisos definido.")

    commands(text, lambda msg: telegram.send(cid, msg), ("telegram", str(cid)))


def handle_wa(u):
    """Comandos do WhatsApp: só de WHATSAPP_ADMINS (ou do próprio número), no grupo geral ou de tema."""
    jid = u["jid"]
    if not whatsapp.is_admin(u):
        return
    known = jid == whatsapp.group_id() or db.theme_of_chat("whatsapp", jid) is not None
    # no privado com o bot o admin pode dar qualquer comando
    if not known and not whatsapp.is_dm(jid) and not u["text"].lower().startswith("/tema"):
        return
    commands(u["text"], lambda msg: whatsapp.send(jid, msg), ("whatsapp", jid))


def commands(text, reply, ctx):
    parts = text.strip().split()
    cmd = parts[0].split("@")[0].lower()

    if themes.command(cmd, text, reply, ctx):
        return
    if cmd in ("/start", "/help"):
        reply(HELP)
    elif cmd == "/add":
        body = text.split(None, 1)[1] if len(parts) > 1 else ""
        main_part, _, name = body.partition("|")
        args = main_part.split()
        if not args:
            return reply("Uso: /add &lt;url&gt; [preço-alvo] [seletor]")
        url = scraper.normalize_url(args[0])
        target = scraper.parse_price(args[1]) if len(args) > 1 else None
        selector = " ".join(args[2:]) or None
        try:
            info = scraper.fetch_info(url, selector)
        except Exception as e:
            return reply(f"Não consegui ler o preço: {html.escape(str(e))}")
        price, title = info["price"], info["title"]
        name = name.strip() or title[:80] or url[:50]
        units = scraper.parse_units(title)
        try:
            db.add_item(name, url, target, selector, units)
        except Exception:
            return reply("Esse link já está sendo monitorado.")
        item = [i for i in db.list_items() if i["url"] == url][0]
        db.record_price(item["id"], price)
        msg = f"✅ Monitorando <b>{html.escape(name)}</b> (id {item['id']})\nPreço atual: {brl(price)}"
        if units:
            msg += f"\n{units} unidades: {per_unit(price, units)}"
        reply(f"{msg}\n{url}")
    elif cmd == "/lista":
        items = db.list_items()
        if not items:
            return reply("Nenhum item. Use /add.")
        out = []
        for i in items:
            now = brl(i["last_price"]) if i["last_price"] else "-"
            pu = per_unit(i["last_price"], i["units"])
            now += f" ({pu})" if pu else ""
            alvo = f" (alvo {brl(i['target'])})" if i["target"] else ""
            out.append(f"{i['id']}. <b>{html.escape(i['name'])}</b>: {now}{alvo}")
        reply("\n".join(out))
    elif cmd == "/remover":
        ok = len(parts) > 1 and parts[1].isdigit() and db.remove_item(int(parts[1]))
        reply("Removido." if ok else "Id não encontrado. Veja /lista.")
    elif cmd == "/categoria":
        body = text.split(None, 1)[1] if len(parts) > 1 else ""
        main_part, _, name = body.partition("|")
        args = main_part.split()
        if not args:
            return reply("Uso: /categoria &lt;link da busca&gt; [alvo] | Nome")
        url = scraper.normalize_url(args[0])
        if not scraper.is_amazon(url):
            return reply("Por enquanto só links de busca da Amazon.")
        max_unit = max_total = None
        for tok in args[1:]:
            tok = tok.lower()
            if tok.endswith("/un"):
                max_unit = scraper.parse_price(tok[:-3])
            else:
                max_total = scraper.parse_price(tok)
        name = name.strip() or "Categoria"
        try:
            w = db.add_watch(name, url, max_unit, max_total)
        except Exception:
            return reply("Essa busca já está sendo monitorada.")
        reply(
            f"✅ Monitorando <b>{html.escape(name)}</b> (id {w['id']}): {describe_target(w)}. Lendo...",
        )
        try:
            hits = check_watch(w)
        except Exception as e:
            db.remove_watch(w["id"])
            return reply(f"Não consegui ler a listagem: {html.escape(str(e))}")
        if not hits:
            return reply("Nada que passe nos alvos agora. Aviso quando aparecer.")
        enqueue_hits(w, hits)
        reply(
            f"Achei {len(hits)} produto(s). Vou postar 1 por minuto no grupo, "
            f"alternando entre as categorias. Veja a fila com /fila.",
        )
    elif cmd == "/categorias":
        ws = db.list_watches()
        if not ws:
            return reply("Nenhuma busca. Use /categoria.")
        reply(
            "\n".join(f"{w['id']}. <b>{html.escape(w['name'])}</b>: {describe_target(w)}" for w in ws),
        )
    elif cmd == "/remover_cat":
        ok = len(parts) > 1 and parts[1].isdigit() and db.remove_watch(int(parts[1]))
        reply("Removida." if ok else "Id não encontrado. Veja /categorias.")
    elif cmd == "/fila":
        counts = db.queue_counts()
        if not counts:
            return reply("Fila vazia.")
        names = {w["id"]: w["name"] for w in db.list_watches()}
        lines = [f"{names.get(i, i)}: {n}" for i, n in sorted(counts.items())]
        total = sum(counts.values())
        reply(f"📮 Fila ({total}, 1 por minuto)\n" + "\n".join(lines))
    elif cmd == "/unidades":
        ok = (
            len(parts) > 2
            and parts[1].isdigit()
            and parts[2].isdigit()
            and int(parts[2]) > 0
            and db.set_units(int(parts[1]), int(parts[2]))
        )
        reply("Unidades atualizadas." if ok else "Uso: /unidades &lt;id&gt; &lt;quantidade&gt;")
    elif cmd == "/checar":
        reply("Checando...")
        check_all()
        reply("Pronto.")


def run():
    interval = int(os.getenv("CHECK_INTERVAL_MIN", "60")) * 60
    post_every = int(os.getenv("POST_INTERVAL_SEC", "60"))
    next_check = next_send = 0
    offset = None
    print("Bot rodando. Ctrl+C para sair.")
    while True:
        if time.time() >= next_check:
            check_all()
            next_check = time.time() + interval
        queued = bool(db.queue_counts())
        if queued and time.time() >= next_send:
            send_next()
            next_send = time.time() + post_every
        themes.search_tick()  # no máximo 1 busca por vez, com pausa entre elas
        themes.post_tick()
        # a espera do getUpdates não passa do próximo envio/checagem
        deadline = min(next_check, next_send) if queued else next_check
        # com WhatsApp ligado, espera curta para responder rápido no grupo
        cap = 3 if whatsapp.enabled() else (20 if themes.has_work() else 25)
        wait = int(max(1, min(cap, deadline - time.time())))
        if whatsapp.enabled():
            try:
                for u in whatsapp.get_updates():
                    if u["text"].startswith("/"):
                        handle_wa(u)
            except Exception as e:
                print(f"[erro whatsapp] {e}")
        try:
            for u in telegram.get_updates(offset, timeout=wait):
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
        # temas: busca os termos vencidos (com pausa) e posta 1 oferta por tema na janela
        while db.due_term(themes.term_interval()):
            themes.search_tick(force=True)
            time.sleep(themes.term_gap())
        themes.post_tick()
        while has_channel() and db.queue_counts():  # sem loop de bot: esvazia a fila antes de sair
            send_next()
            time.sleep(int(os.getenv("POST_INTERVAL_SEC", "60")))
    else:
        run()
