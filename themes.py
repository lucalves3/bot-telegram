"""Temas: grupos de nicho (ex.: Amamentação) alimentados por termos de busca na Amazon.

Cada tema tem termos de busca, filtros (desconto mínimo, preço máximo, palavras proibidas),
um ritmo de postagem com janela de horário e os grupos (Telegram/WhatsApp) que recebem as ofertas.
"""
import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import affiliate
import db
import messages
import scraper
import telegram
import whatsapp
from messages import brl

BRT = timezone(timedelta(hours=-3))
QUEUE_MAX_AGE = 6 * 3600  # oferta na fila há mais de 6h é descartada (preço pode ter mudado)


def term_interval():
    """Cada termo é buscado no máximo a cada TERM_CHECK_MIN minutos."""
    return int(os.getenv("TERM_CHECK_MIN", "120")) * 60


def term_gap():
    """Pausa mínima entre duas buscas na Amazon (evita bloqueio)."""
    return int(os.getenv("TERM_GAP_SEC", "90"))


_last_search = 0.0


# ---------- busca ----------
def _excluded(title, theme):
    words = [w.strip().lower() for w in theme["excludes"].split(",") if w.strip()]
    t = title.lower()
    return any(w in t for w in words)


def check_term(term_row):
    """Busca um termo e coloca na fila do tema as ofertas que passam nos filtros. Devolve quantas."""
    theme = db.theme_by_id(term_row["theme_id"])
    db.touch_term(term_row["id"])
    if not theme:
        return 0
    term = term_row["term"]
    seen = db.theme_seen(theme["id"])
    added = 0
    is_link = term.lower().startswith("http")
    url = scraper.normalize_url(term) if is_link else scraper.search_url(term)
    for p in scraper.fetch_listing(url, pages=1):
        if p["discount"] < theme["min_discount"]:
            continue  # só promoção de verdade (preço "De" riscado)
        if theme["max_total"] is not None and p["price"] > theme["max_total"]:
            continue
        if _excluded(p["title"], theme) or (not is_link and not scraper.relevant(p["title"], term)):
            continue
        last = seen.get(p["asin"])
        if last is not None and p["price"] >= last * 0.95:
            continue  # já postado e não ficou 5% mais barato
        payload = {
            "title": p["title"],
            "price": p["price"],
            "list_price": p["list_price"],
            "discount": p["discount"],
            "units": p["units"],
            "size": scraper.parse_size(p["title"], p["units"]),
            "image": p.get("image"),
            "asin": p["asin"],
            "seen_at": datetime.now(BRT).strftime("%H:%M"),
        }
        db.tq_add(theme["id"], p["asin"], -p["discount"], json.dumps(payload))
        db.set_theme_seen(theme["id"], p["asin"], p["price"])
        added += 1
    return added


def search_tick(force=False):
    """Busca no máximo um termo por chamada, respeitando a pausa entre buscas."""
    global _last_search
    if not force and time.time() - _last_search < term_gap():
        return
    row = db.due_term(term_interval())
    if not row:
        return
    _last_search = time.time()
    try:
        n = check_term(row)
        print(f"[tema] '{row['term']}': {n} oferta(s) na fila")
    except Exception as e:
        print(f"[tema] '{row['term']}': {e}")
        if "503" in str(e) or "429" in str(e):
            # a Amazon recusou: para todas as buscas por 15 min em vez de insistir
            _last_search = time.time() + 15 * 60
            print("[tema] Amazon limitou as buscas; pausando 15 min")


# ---------- postagem ----------
def in_window(theme, now=None):
    h = (now or datetime.now(BRT)).hour
    start, end = theme["hour_start"], theme["hour_end"]
    return start <= h < end if start < end else (h >= start or h < end)


def _send(channel, chat, text, image):
    if channel == "telegram":
        if image:
            try:
                return telegram.send_photo(chat, image, text)
            except Exception as e:
                print(f"[tema foto] {e}")
        return telegram.send(chat, text, preview=not image)
    if image:
        try:
            return whatsapp.send(chat, text, image)
        except Exception as e:
            print(f"[tema whatsapp foto] {e}")
    return whatsapp.send(chat, text)


def post_tick():
    """Posta a melhor oferta de cada tema cuja vez chegou (intervalo + janela de horário)."""
    now = time.time()
    for theme in db.list_themes():
        if now < theme["next_post_at"] or not in_window(theme):
            continue
        groups = db.theme_groups(theme["id"])
        if not groups:
            continue
        row = db.tq_next(theme["id"], QUEUE_MAX_AGE)
        if not row:
            continue
        p = json.loads(row["payload"])
        url = f"https://www.amazon.com.br/dp/{p['asin']}"
        sent = 0
        for g in groups:
            link = affiliate.link(url, g["channel"], theme["tag"])
            try:
                _send(g["channel"], g["chat_id"], messages.product_caption(p, theme["name"], link), p.get("image"))
                sent += 1
            except Exception as e:
                print(f"[tema envio] {theme['name']} -> {g['channel']} {g['chat_id']}: {e}")
        if sent:
            db.tq_done(row)
        else:
            db.tq_fail(row)
        db.update_theme(theme["id"], next_post_at=now + theme["interval_min"] * 60)


def has_work():
    return bool(db.list_themes())


# ---------- comandos ----------
HELP = (
    "\n\n<b>Temas (grupos de nicho)</b>\n"
    "/tema &lt;Nome&gt; - mande dentro do grupo para ligá-lo ao tema (cria se não existir)\n"
    "/termos &lt;Nome&gt; termo 1, termo 2 - termos buscados na Amazon (ou links de busca colados)\n"
    "/remover_termo &lt;Nome&gt; termo\n"
    "/excluir &lt;Nome&gt; palavra 1, palavra 2 - ignora produtos com essas palavras\n"
    "/config &lt;Nome&gt; desconto=15 intervalo=25 horario=8-22 max=150 tag=seunome-20\n"
    "/temas - lista os temas\n"
    "/sair - desliga este grupo do tema\n"
    "WhatsApp no privado com o bot: /grupos lista os grupos, /ligar &lt;Nome&gt; &lt;nº&gt; e /desligar &lt;nº&gt;\n"
    "/remover_tema &lt;Nome&gt;\n"
    "Nome do tema é uma palavra só (ex.: Amamentação)."
)


def _describe(t):
    groups = db.theme_groups(t["id"])
    tg = sum(g["channel"] == "telegram" for g in groups)
    wa = sum(g["channel"] == "whatsapp" for g in groups)
    terms = db.list_terms(t["id"])
    queued = db.tq_counts().get(t["id"], 0)
    lines = [
        f"<b>{html.escape(t['name'])}</b>",
        f"Grupos: {tg} Telegram, {wa} WhatsApp | Fila: {queued}",
        f"Desconto mín.: {t['min_discount']}% | Máx.: {brl(t['max_total']) if t['max_total'] else 'sem limite'}",
        f"1 oferta a cada {t['interval_min']} min, das {t['hour_start']}h às {t['hour_end']}h",
        f"Tag: {t['tag'] or 'padrão do canal'}",
        f"Termos: {html.escape(', '.join(terms)) or '(nenhum, use /termos)'}",
    ]
    if t["excludes"]:
        lines.append(f"Excluídas: {html.escape(t['excludes'])}")
    return "\n".join(lines)


def _split_list(s):
    return [x.strip() for x in s.split(",") if x.strip()]


def command(cmd, text, reply, ctx):
    """Trata os comandos de tema. ctx = (canal, chat_id). Devolve True se tratou."""
    rest = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
    name, _, args = rest.partition(" ")
    args = args.strip()

    if cmd == "/temas":
        themes = db.list_themes()
        reply("\n\n".join(_describe(t) for t in themes) if themes else "Nenhum tema. Use /tema &lt;Nome&gt; no grupo.")
        return True
    channel, chat = ctx
    private = channel == "whatsapp" and whatsapp.is_dm(chat)

    if cmd == "/grupos":
        if channel != "whatsapp":
            reply("No Telegram, mande /tema &lt;Nome&gt; dentro do grupo.")
            return True
        lines = []
        for i, g in enumerate(whatsapp.groups(), 1):
            tid = db.theme_of_chat("whatsapp", g["id"])
            t = db.theme_by_id(tid) if tid else None
            lines.append(f"{i}. {html.escape(g.get('subject') or g['id'])}" + (f" → {html.escape(t['name'])}" if t else ""))
        reply("\n".join(lines) + "\n\nUse /ligar &lt;Tema&gt; &lt;nº&gt; ou /desligar &lt;nº&gt;." if lines else "O bot não está em nenhum grupo.")
        return True
    if cmd in ("/ligar", "/desligar"):
        num = (args if cmd == "/ligar" else name).strip()
        gs = whatsapp.groups() if channel == "whatsapp" else []
        if not num.isdigit() or not 1 <= int(num) <= len(gs) or (cmd == "/ligar" and not name):
            reply("Uso: /ligar &lt;Tema&gt; &lt;nº&gt; ou /desligar &lt;nº&gt; (veja os números em /grupos)")
            return True
        g = gs[int(num) - 1]
        label = html.escape(g.get("subject") or g["id"])
        if cmd == "/desligar":
            ok = db.unlink_group("whatsapp", g["id"])
            reply(f"{label} desligado do tema." if ok else f"{label} não estava em nenhum tema.")
            return True
        t = db.add_theme(name)
        db.link_group(t["id"], "whatsapp", g["id"])
        reply(f"✅ {label} agora recebe o tema <b>{html.escape(t['name'])}</b>.")
        return True
    if cmd == "/sair":
        if private:
            reply("No privado use /desligar &lt;nº&gt; (veja /grupos).")
            return True
        ok = db.unlink_group(*ctx)
        reply("Grupo desligado do tema." if ok else "Este grupo não está em nenhum tema.")
        return True
    if cmd not in ("/tema", "/termos", "/remover_termo", "/excluir", "/config", "/remover_tema"):
        return False
    if not name:
        reply(f"Uso: {cmd} &lt;Nome do tema&gt; ...")
        return True

    if cmd == "/tema":
        t = db.add_theme(name)
        if private:
            reply(f"Tema <b>{html.escape(t['name'])}</b> pronto. Agora ligue os grupos: /grupos e /ligar {html.escape(t['name'])} &lt;nº&gt;.")
            return True
        db.link_group(t["id"], *ctx)
        reply(f"✅ Este grupo agora recebe o tema <b>{html.escape(t['name'])}</b>.\n\n{_describe(t)}")
        return True

    t = db.get_theme(name)
    if not t:
        reply(f"Tema '{html.escape(name)}' não existe. Veja /temas.")
        return True

    if cmd == "/termos":
        terms = _split_list(args)
        if not terms:
            reply("Uso: /termos &lt;Nome&gt; termo 1, termo 2")
            return True
        db.add_terms(t["id"], terms)
        reply(f"Termos adicionados. Vou buscar aos poucos para não ser bloqueado.\n\n{_describe(t)}")
    elif cmd == "/remover_termo":
        reply("Termo removido." if db.remove_term(t["id"], args) else "Termo não encontrado. Veja /temas.")
    elif cmd == "/excluir":
        words = _split_list(t["excludes"]) + _split_list(args)
        db.update_theme(t["id"], excludes=", ".join(dict.fromkeys(w.lower() for w in words)))
        reply(_describe(db.theme_by_id(t["id"])))
    elif cmd == "/config":
        fields = {}
        for key, val in re.findall(r"(\w+)=(\S+)", args):
            key = key.lower()
            if key == "desconto" and val.isdigit():
                fields["min_discount"] = int(val)
            elif key == "intervalo" and val.isdigit() and int(val) > 0:
                fields["interval_min"] = int(val)
            elif key == "horario" and re.fullmatch(r"\d{1,2}-\d{1,2}", val):
                a, b = (int(x) for x in val.split("-"))
                if 0 <= a <= 23 and 0 <= b <= 24:
                    fields["hour_start"], fields["hour_end"] = a, b
            elif key == "max":
                fields["max_total"] = None if val in ("0", "-") else scraper.parse_price(val)
            elif key == "tag":
                fields["tag"] = None if val in ("0", "-") else val
        if not fields:
            reply("Uso: /config &lt;Nome&gt; desconto=15 intervalo=25 horario=8-22 max=150 tag=seunome-20")
            return True
        db.update_theme(t["id"], **fields)
        reply(_describe(db.theme_by_id(t["id"])))
    elif cmd == "/remover_tema":
        db.remove_theme(t["id"])
        reply("Tema removido.")
    return True
