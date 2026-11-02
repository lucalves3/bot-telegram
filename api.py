"""API HTTP do servidor para o coletor do PC (collector.py).

O Railway é bloqueado pela Amazon (IP de datacenter); o PC busca e manda as ofertas para cá.
  GET  /api/terms   -> [{theme: {...filtros}, term}]   termos da Amazon de todos os temas
  POST /api/offers  -> {theme_id, offers: [...]}  ->  {added}
Toda chamada precisa do cabeçalho Authorization: Bearer <INGEST_TOKEN>.
"""
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import db
import themes

THEME_KEYS = ("id", "name", "min_discount", "max_total", "excludes")


def _token():
    return os.getenv("INGEST_TOKEN", "")


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
        if not self._authorized():
            return self._json(401, {"error": "não autorizado"})
        if self.path == "/api/terms":
            out = []
            for t in db.list_themes():
                theme = {k: t[k] for k in THEME_KEYS}
                out += [{"theme": theme, "term": term} for term in db.list_terms(t["id"]) if not term.lower().startswith("shopee:")]
            return self._json(200, out)
        self._json(404, {"error": "não encontrado"})

    def do_POST(self):
        if not self._authorized():
            return self._json(401, {"error": "não autorizado"})
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

    def log_message(self, *args):
        pass  # sem log de cada requisição


def start():
    """Sobe a API numa thread se INGEST_TOKEN e PORT (o Railway define PORT) estiverem configurados."""
    port = os.getenv("PORT")
    if not _token() or not port:
        return
    server = ThreadingHTTPServer(("0.0.0.0", int(port)), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[api] coletor pode enviar ofertas na porta {port}")
