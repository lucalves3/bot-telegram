import json
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


AMAZON_HOST = re.compile(r"(^|\.)amazon\.[a-z.]+$")
AMAZON_ASIN = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?]|$)")
TRACKING_PARAMS = re.compile(r"^(utm_.*|gclid|fbclid|msclkid|ref|ref_|tag|ascsubtag)$", re.I)

# seletor usado quando a loja não expõe o preço em JSON-LD/meta
DEFAULT_SELECTORS = {"amazon": ".apex-core-price-identifier .a-offscreen"}


def _store(url):
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    return "amazon" if AMAZON_HOST.search(host) else None


def is_amazon(url):
    return _store(url) == "amazon"


def normalize_url(url):
    """Remove rastreamento do link; na Amazon reduz para /dp/<ASIN>."""
    parts = urlsplit(url.strip())
    if _store(url) == "amazon":
        m = AMAZON_ASIN.search(parts.path + "/")
        if m:
            return f"{parts.scheme}://{parts.netloc}/dp/{m.group(1)}"
    query = [(k, v) for k, v in parse_qsl(parts.query) if not TRACKING_PARAMS.match(k)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


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


UNITS_RE = re.compile(r"(\d{1,4})\s*(?:unidades?|un\b|fraldas?|tiras?|pe[cç]as?)", re.I)


PACK_RE = re.compile(r"(?:pacote|pct)\s+(?:com|de)\s+(\d{1,4})\b", re.I)


def parse_units(title):
    """Quantidade de unidades do pacote lida do título ('46 Unidades' -> 46)."""
    m = UNITS_RE.search(title or "") or PACK_RE.search(title or "")
    return int(m.group(1)) if m else None


def _title(soup):
    el = soup.select_one("#productTitle")
    if el:
        return el.get_text(strip=True)
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return og["content"].strip()
    return soup.title.get_text(strip=True) if soup.title else ""


def _image(soup):
    og = soup.find("meta", attrs={"property": "og:image"})
    if og and og.get("content"):
        return og["content"]
    img = soup.select_one("#landingImage")
    return (img.get("src") if img else None) or None


def fetch_price(url, selector=None):
    return fetch_info(url, selector)["price"]


def fetch_info(url, selector=None):
    """Retorna {'price': float, 'title': str} ou levanta RuntimeError se não achar o preço."""
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    title = _title(soup)
    image = _image(soup)

    selector = selector or DEFAULT_SELECTORS.get(_store(url))
    if selector:
        el = soup.select_one(selector)
        if el:
            p = parse_price(el.get("content") or el.get_text())
            if p:
                return {"price": p, "title": title, "image": image}
        raise RuntimeError(f"seletor '{selector}' não encontrou preço")

    price = _from_jsonld(soup) or _from_meta(soup)
    if price:
        return {"price": price, "title": title, "image": image}
    raise RuntimeError("preço não encontrado automaticamente; informe um seletor CSS")


# produtos que aparecem na busca de fralda mas não interessam
EXCLUDE_WORDS = ("geriátric", "geriatric", "adulto", "incontin", "absorvente")


def fetch_listing(url, pages=2):
    """Lê cards de uma busca da Amazon: [{asin, title, price, units}]."""
    out, seen = [], set()
    parts = urlsplit(url)
    base_query = [(k, v) for k, v in parse_qsl(parts.query) if k != "page"]
    for page in range(1, pages + 1):
        query = urlencode(base_query + [("page", str(page))])
        page_url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
        r = requests.get(page_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select('div[data-component-type="s-search-result"]')
        if not cards and page == 1:
            raise RuntimeError("nenhum produto lido na listagem (bloqueio ou link inválido)")
        for card in cards:
            asin = card.get("data-asin")
            h2 = card.select_one("h2")
            price_el = card.select_one("span.a-price:not(.a-text-price) .a-offscreen")
            price = parse_price(price_el.get_text()) if price_el else None
            title = h2.get_text(" ", strip=True) if h2 else ""
            if not asin or asin in seen or not price or not title:
                continue
            seen.add(asin)
            img = card.select_one("img.s-image")
            out.append(
                {
                    "asin": asin,
                    "title": title,
                    "price": price,
                    "units": parse_units(title),
                    "image": img.get("src") if img else None,
                }
            )
        time.sleep(2)
    return out
