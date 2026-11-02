"""API HTTP do servidor para o coletor do PC (collector.py).

O Railway é bloqueado pela Amazon (IP de datacenter); o PC busca e manda as ofertas para cá.
  GET  /api/terms   -> [{theme: {...filtros}, term}]   termos da Amazon de todos os temas
  POST /api/offers  -> {theme_id, offers: [...]}  ->  {added}
Toda chamada precisa do cabeçalho Authorization: Bearer <INGEST_TOKEN>.
"""
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import db
import ml
import scraper
import themes

PAINEL = Path(__file__).parent / "painel.html"

THEME_KEYS = ("id", "name", "min_discount", "max_total", "excludes")


def _token():
    return token()


def token():
    """INGEST_TOKEN, ou um derivado do TELEGRAM_TOKEN (já existe no PC e no servidor, nada a copiar)."""
    if os.getenv("INGEST_TOKEN"):
        return os.getenv("INGEST_TOKEN")
    tg = os.getenv("TELEGRAM_TOKEN", "")
    return hashlib.sha256(f"price-hunter-ingest:{tg}".encode()).hexdigest() if tg else ""


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return bool(_token()) and hmac.compare_digest(got, _token())

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"ok": True})
        if self.path.split("?")[0] == "/painel":
            page = PAINEL.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            return self.wfile.write(page)
        if self.path.startswith("/ml/callback"):
            return self._ml_callback()
        if not self._authorized():
            return self._json(401, {"error": "chave inválida"})
        if self.path.startswith("/api/ml/get"):
            # diagnóstico: GET numa rota da API do Mercado Livre com o login do bot (só leitura)
            path = dict(parse_qsl(urlsplit(self.path).query)).get("path", "/users/me")
            if not path.startswith("/") or "://" in path:
                return self._json(400, {"error": "path inválido"})
            try:
                r = ml.get(path)
                return self._json(200, {"status": r.status_code, "body": r.text[:3000]})
            except Exception as e:
                return self._json(502, {"error": str(e)[:300]})
        if self.path.startswith("/api/ml/search"):
            # teste: busca no Mercado Livre e mostra o resumo do que voltou
            q = dict(parse_qsl(urlsplit(self.path).query)).get("q", "fralda")
            try:
                res = ml.search(q)
            except Exception as e:
                return self._json(502, {"error": str(e)[:300]})
            return self._json(200, {"total": len(res), "com_desconto": sum(r["discount"] > 0 for r in res), "exemplos": res[:3]})
        if self.path == "/api/ml":
            return self._json(200, {"enabled": ml.enabled(), "connected": ml.connected(), "login": ml.login_url() if ml.enabled() else None})
        if self.path == "/api/status":
            # fila por tema e última busca de cada termo
            counts = db.tq_counts()
            with db.conn() as c:
                terms = c.execute("SELECT theme_id, term, last_checked FROM theme_terms ORDER BY theme_id, term").fetchall()
                queue = c.execute("SELECT theme_id, asin, prio, ts FROM theme_queue").fetchall()
            return self._json(200, {
                "fila": {str(k): v for k, v in counts.items()},
                "fila_por_loja": {
                    "ml": sum(q["asin"].startswith("ml:") for q in queue),
                    "shopee": sum(q["asin"].startswith("shopee:") for q in queue),
                    "amazon": sum(not q["asin"].startswith(("ml:", "shopee:")) for q in queue),
                },
                "termos": [{"tema": t["theme_id"], "termo": t["term"][:60], "buscado_ha_min": round((time.time() - t["last_checked"]) / 60) if t["last_checked"] else None} for t in terms],
            })
        if self.path == "/api/themes":
            return self._json(200, [{"id": t["id"], "name": t["name"]} for t in db.list_themes()])
        if self.path == "/api/terms":
            out = []
            for t in db.list_themes():
                theme = {k: t[k] for k in THEME_KEYS}
                out += [{"theme": theme, "term": term} for term in db.list_terms(t["id"]) if not term.lower().startswith("shopee:")]
            return self._json(200, out)
        self._json(404, {"error": "não encontrado"})

    def do_POST(self):
        if not self._authorized():
            return self._json(401, {"error": "chave inválida"})
        if self.path == "/api/manual":
            return self._manual()
        if self.path == "/api/terms/remove":
            try:
                b = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                tid = int(b["theme_id"])
                removed = [x for x in b.get("terms", []) if isinstance(x, str) and db.remove_term(tid, x)]
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"error": "corpo inválido"})
            with db.conn() as c:  # tira da fila o que veio desses termos
                for x in removed:
                    c.execute("DELETE FROM theme_queue WHERE theme_id = ? AND json_extract(payload, '$.term') = ?", (tid, x))
            return self._json(200, {"removed": removed, "terms": db.list_terms(tid)})
        if self.path == "/api/terms":
            # adiciona termos a um tema (mesmo efeito do /termos no WhatsApp)
            try:
                b = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                t = db.theme_by_id(int(b["theme_id"]))
                terms = [x.strip() for x in b.get("terms", []) if isinstance(x, str) and x.strip()]
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"error": "corpo inválido"})
            if not t or not terms:
                return self._json(400, {"error": "tema ou termos inválidos"})
            db.add_terms(t["id"], terms)
            return self._json(200, {"terms": db.list_terms(t["id"])})
        if self.path != "/api/offers":
            return self._json(404, {"error": "não encontrado"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            theme_id = int(body["theme_id"])
            offers = [o for o in body.get("offers", []) if o.get("asin") and isinstance(o.get("price"), (int, float))]
        except (ValueError, KeyError, TypeError):
            return self._json(400, {"error": "corpo inválido"})
        if not db.theme_by_id(theme_id):
            return self._json(404, {"error": "tema não existe"})
        added = themes.enqueue(theme_id, offers)
        print(f"[coletor] tema {theme_id}: {len(offers)} recebida(s), {added} na fila")
        self._json(200, {"added": added})

    def _html(self, code, text):
        page = f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><body style='font:16px system-ui;padding:24px'>{text}".encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page)

    def _ml_callback(self):
        """Volta do login do Mercado Livre: troca o código pelo token e guarda no banco."""
        q = dict(parse_qsl(urlsplit(self.path).query))
        if not ml.valid_state(q.get("state")) or not q.get("code"):
            return self._html(400, "❌ Pedido de login inválido ou vencido. Tente de novo pelo painel.")
        try:
            ml.exchange(q["code"])
        except Exception as e:
            print(f"[ml] login falhou: {e}")
            return self._html(500, "❌ O Mercado Livre recusou o login. Veja os logs do Railway.")
        print("[ml] conta do Mercado Livre conectada")
        self._html(200, "✅ Mercado Livre conectado ao bot! Pode fechar esta página.")

    def _manual(self):
        """Oferta escolhida no painel: entra na frente da fila (mesmo se já postada antes)."""
        try:
            b = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            theme_id = int(b["theme_id"])
        except (ValueError, KeyError, TypeError):
            return self._json(400, {"error": "escolha o tema"})
        url = b.get("url", "")
        aff = (b.get("aff") or "").strip()
        price = scraper.parse_price(str(b.get("price", "")))
        title = (b.get("title") or "").strip()
        if not db.theme_by_id(theme_id):
            return self._json(404, {"error": "tema não existe"})
        if re.search(r"mercadoli[vb]re\.com|meli\.la", url + aff):
            # Mercado Livre: o link de afiliado (meli.la) vem pronto da barra "Compartilhar"
            mlb = re.search(r"MLB-?(\d+)", url)
            if not re.match(r"https://meli\.la/\w+$", aff):
                return self._json(400, {"error": "cole o link de afiliado meli.la (botão Compartilhar do Mercado Livre)"})
            product_id, link = f"ml:MLB{mlb.group(1)}" if mlb else f"ml:{aff.rsplit('/', 1)[1]}", aff
        else:
            asin = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", url)
            if not asin:
                return self._json(400, {"error": "link da Amazon sem código do produto (/dp/...)"})
            product_id, link = asin.group(1), None  # Amazon: tag de afiliado é posta na hora de postar
        if not price or not title:
            return self._json(400, {"error": "preencha título e preço"})
        list_price = scraper.parse_price(str(b.get("list_price", ""))) or None
        if list_price and list_price <= price:
            list_price = None
        discount = round((1 - price / list_price) * 100) if list_price else 0
        units = scraper.parse_units(title)
        offer = {
            "title": title,
            "price": price,
            "list_price": list_price,
            "discount": discount,
            "units": units,
            "size": scraper.parse_size(title, units),
            "image": (b.get("image") or "").strip() or None,
            "asin": product_id,
            "url": link,
            "flash": bool(b.get("flash")),
            "manual": True,
            "seen_at": datetime.now(themes.BRT).strftime("%H:%M"),
        }
        # escolhida a dedo: 50 pontos à frente das achadas pela busca
        db.tq_add(theme_id, product_id, -discount - 50, json.dumps(offer))
        db.set_theme_seen(theme_id, product_id, price)
        print(f"[painel] tema {theme_id}: {title[:50]} ({discount}% off){' RELÂMPAGO' if offer['flash'] else ''}")
        self._json(200, {"ok": True, "discount": discount, "flash": offer["flash"]})

    def log_message(self, *args):
        pass  # sem log de cada requisição


if __name__ == "__main__" and sys.argv[1:] == ["token"]:
    # mostra a chave do painel (derivada do TELEGRAM_TOKEN do .env)
    from dotenv import load_dotenv

    load_dotenv()
    print(token())


def start():
    """Sobe a API numa thread se INGEST_TOKEN e PORT (o Railway define PORT) estiverem configurados."""
    port = os.getenv("PORT")
    if not _token() or not port:
        return
    server = ThreadingHTTPServer(("0.0.0.0", int(port)), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[api] coletor pode enviar ofertas na porta {port}")
