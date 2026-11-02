"""Mercado Livre: busca de ofertas pela API oficial (app de desenvolvedor + login OAuth da sua conta).

Variáveis: ML_CLIENT_ID, ML_CLIENT_SECRET, PUBLIC_URL (ex.: https://worker-production-7f73.up.railway.app).
Link de afiliado: o link do produto com matt_word (etiqueta) e matt_tool, os mesmos parâmetros
que o link curto meli.la usa. ML_AFF_WORD / ML_AFF_TOOL.
"""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

import requests

import db

AUTH = "https://auth.mercadolivre.com.br/authorization"
API = "https://api.mercadolibre.com"


def enabled():
    return bool(os.getenv("ML_CLIENT_ID") and os.getenv("ML_CLIENT_SECRET"))


def redirect_uri():
    return os.getenv("PUBLIC_URL", "").rstrip("/") + "/ml/callback"


def _state():
    """Assinatura do pedido de login, para ninguém mais ligar outra conta ao bot."""
    day = str(int(time.time() // 86400))
    return day + "." + hmac.new(os.getenv("ML_CLIENT_SECRET", "").encode(), day.encode(), hashlib.sha256).hexdigest()[:24]


def valid_state(state):
    return any(hmac.compare_digest(state or "", s) for s in (_state(),))


def login_url():
    q = {"response_type": "code", "client_id": os.getenv("ML_CLIENT_ID", ""), "redirect_uri": redirect_uri(), "state": _state()}
    return f"{AUTH}?{urlencode(q)}"


def _save(tok):
    db.set_setting("ml_token", json.dumps({**tok, "expires_at": time.time() + int(tok.get("expires_in", 21600)) - 300}))


def exchange(code):
    r = requests.post(
        f"{API}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": os.getenv("ML_CLIENT_ID"),
            "client_secret": os.getenv("ML_CLIENT_SECRET"),
            "code": code,
            "redirect_uri": redirect_uri(),
        },
        timeout=30,
    )
    r.raise_for_status()
    _save(r.json())


def connected():
    return bool(db.get_setting("ml_token"))


def _access_token():
    raw = db.get_setting("ml_token")
    if not raw:
        raise RuntimeError("Mercado Livre não conectado: abra /ml/login no painel")
    tok = json.loads(raw)
    if time.time() >= tok["expires_at"]:
        r = requests.post(
            f"{API}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": os.getenv("ML_CLIENT_ID"),
                "client_secret": os.getenv("ML_CLIENT_SECRET"),
                "refresh_token": tok["refresh_token"],
            },
            timeout=30,
        )
        r.raise_for_status()
        _save(r.json())
        tok = json.loads(db.get_setting("ml_token"))
    return tok["access_token"]


def affiliate(url):
    """Link do produto com a etiqueta de afiliado (mesmos parâmetros do meli.la)."""
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("matt_word", "matt_tool")]
    q += [("matt_word", os.getenv("ML_AFF_WORD", "allu5378778")), ("matt_tool", os.getenv("ML_AFF_TOOL", "19307211"))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))


def search(term, limit=50):
    """Ofertas do termo no Mercado Livre, no mesmo formato do scraper da Amazon."""
    if not enabled():
        raise RuntimeError("Mercado Livre não configurado (ML_CLIENT_ID e ML_CLIENT_SECRET)")
    r = requests.get(
        f"{API}/sites/MLB/search",
        params={"q": term, "limit": limit},
        headers={"Authorization": f"Bearer {_access_token()}"},
        timeout=30,
    )
    r.raise_for_status()
    out = []
    for it in r.json().get("results", []):
        price, orig = it.get("price"), it.get("original_price")
        if not price:
            continue
        list_price = orig if orig and orig > price else None
        out.append(
            {
                "id": f"ml:{it['id']}",
                "title": it.get("title", ""),
                "price": float(price),
                "list_price": float(list_price) if list_price else None,
                "discount": round((1 - price / list_price) * 100) if list_price else 0,
                "image": (it.get("thumbnail") or "").replace("-I.jpg", "-O.jpg") or None,
                "url": affiliate(it["permalink"]),
            }
        )
    return out
