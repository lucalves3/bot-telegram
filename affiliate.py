import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def tag(channel=None):
    """Tag de afiliado do canal (AMAZON_TAG_TELEGRAM / AMAZON_TAG_WHATSAPP), ou a geral AMAZON_TAG."""
    if channel:
        specific = os.getenv(f"AMAZON_TAG_{channel.upper()}", "").strip()
        if specific:
            return specific
    return os.getenv("AMAZON_TAG", "").strip()


def link(url, channel=None, override=None):
    """Devolve o link com o código de afiliado (override > canal > geral); sem código, o link puro."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    t = (override or "").strip() or tag(channel)
    if t and ".amazon." in f".{host}":
        query = [(k, v) for k, v in parse_qsl(parts.query) if k != "tag"] + [("tag", t)]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    return url
