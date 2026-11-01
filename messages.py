import html
import random

INTROS = [
    "Olha só o que apareceu! 👀",
    "Achei uma oferta pra você! 🛍️",
    "Bora economizar? 💸",
    "Esse aqui merece uma olhada! ✨",
    "Oferta fresquinha por aqui! 🔥",
]


def brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_amount(amount, kind):
    if amount >= 1000:
        return f"{amount / 1000:g} {'kg' if kind == 'g' else 'L'}"
    return f"{amount:g} {kind}"


def per_size(price, amount, kind):
    """'R$ 0,45 por grama'; para valores pequenos mostra por 100 g/ml."""
    per = price / amount
    if per >= 0.1:
        return f"{brl(per)} por {'grama' if kind == 'g' else 'ml'}"
    return f"{brl(per * 100)} por 100 {kind}"


def product_caption(p, category, url):
    """Mensagem de um produto da fila. p: dict com title, price, units, size, seen_at."""
    lines = [random.choice(INTROS)]
    if category:
        lines.append(f"📂 {html.escape(category)}")
    lines += ["", f"<b>{html.escape(p['title'][:90])}</b>", ""]
    if p.get("list_price"):
        lines.append(f"🏷️ De <s>{brl(p['list_price'])}</s> por <b>{brl(p['price'])}</b> ({p['discount']}% off)")
    else:
        lines.append(f"💰 {brl(p['price'])}")
    units = p.get("units")
    if units and units > 1:
        lines.append(f"📦 {brl(p['price'] / units)} por unidade ({units} un)")
    if p.get("size"):
        amount, kind = p["size"]
        total = " no total" if units and units > 1 else ""
        lines.append(f"⚖️ {per_size(p['price'], amount, kind)} ({fmt_amount(amount, kind)}{total})")
    lines += ["", f"👉 {url}", "", f"🕐 Preço visto às {p['seen_at']}, pode mudar. Vale conferir!"]
    return "\n".join(lines)
