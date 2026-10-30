import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def link(url):
    """Devolve o link com o código de afiliado da loja; sem código configurado, devolve o link puro."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    tag = os.getenv("AMAZON_TAG", "").strip()
    if tag and ".amazon." in f".{host}":
        query = [(k, v) for k, v in parse_qsl(parts.query) if k != "tag"] + [("tag", tag)]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    return url
