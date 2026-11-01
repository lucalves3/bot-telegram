"""Cliente da ponte WhatsApp (whatsapp/index.js, Baileys)."""
import html
import os
import re

import requests


def _base():
    return f"http://127.0.0.1:{os.getenv('WHATSAPP_BRIDGE_PORT', '3001')}"


def group_id():
    return os.getenv("WHATSAPP_GROUP_ID", "").strip()


def enabled():
    """Ponte ligada: há grupo geral configurado ou WHATSAPP_ENABLED=1 (grupos de tema)."""
    return bool(group_id()) or os.getenv("WHATSAPP_ENABLED", "") == "1"


def to_wa(text):
    """Converte o HTML do Telegram (<b>, <i>, entidades) para a formatação do WhatsApp."""
    text = re.sub(r"</?b>", "*", text)
    text = re.sub(r"</?i>", "_", text)
    text = re.sub(r"</?s>", "~", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def send(jid, text, image=None):
    payload = {"jid": jid, "text": to_wa(text)}
    if image:
        payload["image"] = image
    r = requests.post(f"{_base()}/send", json=payload, timeout=60)
    r.raise_for_status()
    return r.json()


def get_updates():
    r = requests.get(f"{_base()}/updates", timeout=10)
    r.raise_for_status()
    return r.json()


def groups():
    """[{id, subject}] dos grupos em que o número do bot está, em ordem alfabética."""
    r = requests.get(f"{_base()}/groups", timeout=20)
    r.raise_for_status()
    return sorted(r.json(), key=lambda g: (g.get("subject") or "").lower())


def is_dm(jid):
    return not jid.endswith("@g.us")


def admins():
    return {re.sub(r"\D", "", a) for a in os.getenv("WHATSAPP_ADMINS", "").split(",") if a.strip()}


def is_admin(update):
    if update.get("from_me"):
        # o próprio número do bot só vale em grupo; no privado seriam conversas pessoais dele
        return not is_dm(update.get("jid", ""))
    number = re.sub(r"\D", "", update.get("sender", "").split("@")[0].split(":")[0])
    return number in admins()
