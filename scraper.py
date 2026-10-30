import json
import re

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


def parse_price(text):
    """Converte '1.299,90', 'R$ 1299.90', '1,299.90' em float."""
    if text is None:
        return None
    s = re.sub(r"[^\d.,]", "", str(text))
    if not s:
        return None
    if "," in s and "." in s:
        # o último separador é o decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _from_jsonld(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                offers = node.get("offers")
                if offers:
                    for o in offers if isinstance(offers, list) else [offers]:
                        if isinstance(o, dict):
                            p = parse_price(o.get("price") or o.get("lowPrice"))
                            if p:
                                return p
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return None


def _from_meta(soup):
    for attrs in (
        {"property": "product:price:amount"},
        {"property": "og:price:amount"},
        {"itemprop": "price"},
    ):
        tag = soup.find(["meta", "span", "div"], attrs=attrs)
        if tag:
            p = parse_price(tag.get("content") or tag.get_text())
            if p:
                return p
    return None


def fetch_price(url, selector=None):
    """Retorna o preço (float) ou levanta RuntimeError se não achar."""
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    if selector:
        el = soup.select_one(selector)
        if el:
            p = parse_price(el.get("content") or el.get_text())
            if p:
                return p
        raise RuntimeError(f"seletor '{selector}' não encontrou preço")

    price = _from_jsonld(soup) or _from_meta(soup)
    if price:
        return price
    raise RuntimeError("preço não encontrado automaticamente; informe um seletor CSS")
