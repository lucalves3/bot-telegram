"""Shopee: busca de ofertas pela Open API oficial de afiliados (affiliate.shopee.com.br > Open API).

Precisa de SHOPEE_APP_ID e SHOPEE_SECRET. Os links devolvidos (offerLink) já são de afiliado.
"""
import hashlib
import json
import os
import time

import requests

API = "https://open-api.affiliate.shopee.com.br/graphql"

# sortType: 1 relevância, 2 mais vendidos, 3 maior preço, 4 menor preço, 5 maior comissão
QUERY = """
query($keyword: String, $sortType: Int, $page: Int, $limit: Int) {
  productOfferV2(keyword: $keyword, sortType: $sortType, page: $page, limit: $limit) {
    nodes { itemId productName priceMin priceMax priceDiscountRate sales imageUrl offerLink shopName commissionRate sellerCommissionRate shopeeCommissionRate }
  }
}
"""


def enabled():
    return bool(os.getenv("SHOPEE_APP_ID") and os.getenv("SHOPEE_SECRET"))


def _call(query, variables, attempts=3):
    """Chama a API; o erro 10000 (System Error) é instabilidade da Shopee: tenta de novo com pausa."""
    for i in range(attempts):
        try:
            return _call_once(query, variables)
        except (RuntimeError, requests.RequestException) as e:
            if i == attempts - 1 or ("10000" not in str(e) and not isinstance(e, requests.RequestException)):
                raise
            time.sleep(3 * (i + 1))


def _call_once(query, variables):
    app_id, secret = os.getenv("SHOPEE_APP_ID", ""), os.getenv("SHOPEE_SECRET", "")
    body = json.dumps({"query": query, "variables": variables}, separators=(",", ":"))
    ts = str(int(time.time()))
    sig = hashlib.sha256(f"{app_id}{ts}{body}{secret}".encode()).hexdigest()
    r = requests.post(
        API,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"SHA256 Credential={app_id}, Timestamp={ts}, Signature={sig}",
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(f"Shopee API: {data['errors'][0].get('message')}")
    return data["data"]


def search(keyword, limit=50, sort=2, pages=1):
    """Ofertas da Shopee para o termo, as mais vendidas primeiro. Mesmo formato do scraper da Amazon.

    pages: quantas páginas de `limit` produtos ler (1 chamada à API por página)."""
    if not enabled():
        raise RuntimeError("Shopee não configurada (SHOPEE_APP_ID e SHOPEE_SECRET)")
    nodes = []
    for page in range(1, pages + 1):
        try:
            got = _call(QUERY, {"keyword": keyword, "sortType": sort, "page": page, "limit": limit})["productOfferV2"]["nodes"] or []
        except Exception:
            if page == 1:
                raise
            break  # páginas extras são bônus: segue com o que já veio
        nodes += got
        if len(got) < limit:
            break
    out, seen = [], set()
    for n in nodes:
        if n.get("itemId") in seen:
            continue
        seen.add(n.get("itemId"))
        try:
            price = float(n["priceMin"])
        except (TypeError, ValueError):
            continue
        discount = int(n.get("priceDiscountRate") or 0)
        list_price = round(price / (1 - discount / 100), 2) if 0 < discount < 100 else None
        out.append(
            {
                "id": f"shopee:{n['itemId']}",
                "title": n["productName"],
                "price": price,
                "list_price": list_price,
                "discount": discount,
                "sales": int(n.get("sales") or 0),
                "image": n.get("imageUrl"),
                "url": n["offerLink"],
                # taxa de comissão (0.08 = 8%): total, parte paga pela loja e parte paga pela Shopee
                "commission": float(n.get("commissionRate") or 0),
                "commission_seller": float(n.get("sellerCommissionRate") or 0),
                "commission_shopee": float(n.get("shopeeCommissionRate") or 0),
            }
        )
    return out
