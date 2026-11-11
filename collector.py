"""Coletor: roda no seu PC, busca os termos dos temas na Amazon e manda as ofertas para o servidor.

Uso:  python collector.py
Precisa no .env: REMOTE_URL (ex.: https://worker-production.up.railway.app) e INGEST_TOKEN (o mesmo do Railway).
O servidor (Railway, com AMAZON_REMOTE=1) guarda na fila e posta nos grupos.
"""
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

import api  # noqa: E402  (depois do .env)
import themes  # noqa: E402

BASE = os.getenv("REMOTE_URL", "").rstrip("/")
HEADERS = {"Authorization": f"Bearer {api.token()}"}


def main():
    if not BASE or not api.token():
        raise SystemExit("Configure REMOTE_URL (e TELEGRAM_TOKEN ou INGEST_TOKEN) no .env")
    last = {}  # (theme_id, termo) -> hora da última busca
    print(f"Coletor rodando, enviando para {BASE}. Ctrl+C para sair.")
    while True:
        try:
            terms = requests.get(f"{BASE}/api/terms", headers=HEADERS, timeout=30)
            terms.raise_for_status()
            terms = terms.json()
        except Exception as e:
            print(f"[coletor] não consegui pegar os termos: {e}")
            time.sleep(60)
            continue
        due = [t for t in terms if time.time() - last.get((t["theme"]["id"], t["term"]), 0) >= themes.term_interval()]
        if not due:
            time.sleep(60)
            continue
        t = min(due, key=lambda t: last.get((t["theme"]["id"], t["term"]), 0))
        last[(t["theme"]["id"], t["term"])] = time.time()
        label = t["term"][:60]
        try:
            # modo promoção (ligado no servidor com /promo): lê mais páginas da busca
            offers = themes.find_offers(t["theme"], t["term"], pages=themes.PROMO_PAGES if t.get("promo") else 1)
            r = requests.post(
                f"{BASE}/api/offers", headers=HEADERS, json={"theme_id": t["theme"]["id"], "term": t["term"], "offers": offers}, timeout=30
            )
            r.raise_for_status()
            print(f"[coletor] {t['theme']['name']} / {label}: {len(offers)} achada(s), {r.json()['added']} nova(s) na fila")
        except Exception as e:
            print(f"[coletor] {label}: {e}")
            if "503" in str(e) or "429" in str(e):
                print("[coletor] Amazon limitou; pausando 15 min")
                time.sleep(15 * 60)
                continue
        time.sleep(themes.term_gap())


if __name__ == "__main__":
    main()
