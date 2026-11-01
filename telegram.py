import json
import os

import requests

API = "https://api.telegram.org/bot{token}/{method}"


def _call(method, **payload):
    token = os.environ["TELEGRAM_TOKEN"]
    r = requests.post(API.format(token=token, method=method), json=payload, timeout=40)
    r.raise_for_status()
    return r.json()["result"]


def send(chat_id, text, preview=True):
    return _call(
        "sendMessage",
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=not preview,
    )


def send_photo(chat_id, photo_url, caption):
    return _call("sendPhoto", chat_id=chat_id, photo=photo_url, caption=caption[:1024], parse_mode="HTML")


def send_photo_file(chat_id, png, caption):
    """Envia uma imagem (bytes) e devolve o message_id."""
    token = os.environ["TELEGRAM_TOKEN"]
    r = requests.post(
        API.format(token=token, method="sendPhoto"),
        data={"chat_id": chat_id, "caption": caption},
        files={"photo": ("qr.png", png, "image/png")},
        timeout=40,
    )
    r.raise_for_status()
    return r.json()["result"]["message_id"]


def edit_photo_file(chat_id, message_id, png, caption):
    """Troca a imagem de uma mensagem já enviada."""
    token = os.environ["TELEGRAM_TOKEN"]
    media = json.dumps({"type": "photo", "media": "attach://qr", "caption": caption})
    r = requests.post(
        API.format(token=token, method="editMessageMedia"),
        data={"chat_id": chat_id, "message_id": message_id, "media": media},
        files={"qr": ("qr.png", png, "image/png")},
        timeout=40,
    )
    r.raise_for_status()


def delete_message(chat_id, message_id):
    _call("deleteMessage", chat_id=chat_id, message_id=message_id)


def get_updates(offset=None, timeout=25):
    return _call("getUpdates", offset=offset, timeout=timeout, allowed_updates=["message"])
