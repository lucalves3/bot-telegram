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


def get(path, **params):
    """GET autenticado na API (devolve a resposta crua)."""
    return requests.get(f"{API}{path}", params=params or None, headers={"Authorization": f"Bearer {_access_token()}"}, timeout=30)


def affiliate(url):
    """Link do produto com a etiqueta de afiliado (mesmos parâmetros do meli.la)."""
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("matt_word", "matt_tool")]
    q += [("matt_word", os.getenv("ML_AFF_WORD", "allu5378778")), ("matt_tool", os.getenv("ML_AFF_TOOL", "19307211"))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))


BABY_CATEGORY = "MLB1384"  # Bebês


def _json(path, **params):
    r = get(path, **params)
    r.raise_for_status()
    return r.json()


def _products(term, limit):
    """Produtos de catálogo para o termo. 'top' = mais vendidos de Bebês; 'top MLBxxxx' = de outra categoria.
    (A busca comum /sites/MLB/search é bloqueada para apps; o catálogo e os destaques não.)"""
    if term.lower().startswith("top"):
        cat = term[3:].strip() or BABY_CATEGORY
        ids = [c["id"] for c in _json(f"/highlights/MLB/category/{cat}").get("content", []) if c.get("type") == "PRODUCT"]
        return [{"id": pid} for pid in ids[:limit]]
    return _json("/products/search", status="active", site_id="MLB", q=term, limit=50).get("results", [])[: limit * 2]


def search(term, limit=15):
    """Ofertas do termo no Mercado Livre, no mesmo formato do scraper da Amazon.
    Para cada produto do catálogo pega a oferta mais barata e o preço original (para o desconto)."""
    if not enabled():
        raise RuntimeError("Mercado Livre não configurado (ML_CLIENT_ID e ML_CLIENT_SECRET)")
    out = []
    for prod in _products(term, limit):
        if len(out) >= limit:
            break
        pid = prod["id"]
        try:
            items = _json(f"/products/{pid}/items").get("results", [])
        except requests.HTTPError:
            continue  # produto sem ofertas ativas
        items = [i for i in items if i.get("price") and i.get("condition", "new") == "new"]
        if not items:
            continue
        best = min(items, key=lambda i: i["price"])
        price, orig = float(best["price"]), best.get("original_price")
        list_price = float(orig) if orig and orig > price else None
        name, pics = prod.get("name"), prod.get("pictures")
        if not name or pics is None:
            info = _json(f"/products/{pid}")
            name, pics = info.get("name", ""), info.get("pictures") or []
        out.append(
            {
                "id": f"ml:{pid}",
                "title": name,
                "price": price,
                "list_price": list_price,
                "discount": round((1 - price / list_price) * 100) if list_price else 0,
                "image": (pics[0].get("url") if pics else None),
                "url": affiliate(f"https://www.mercadolivre.com.br/p/{pid}"),
            }
        )
    return out
