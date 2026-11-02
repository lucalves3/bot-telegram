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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import db
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
        if not self._authorized():
            return self._json(401, {"error": "chave inválida"})
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
