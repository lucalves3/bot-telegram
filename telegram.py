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


def get_updates(offset=None, timeout=25):
    return _call("getUpdates", offset=offset, timeout=timeout, allowed_updates=["message"])
