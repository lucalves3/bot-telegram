import html
import random
import re

# ml/L no título desses é a capacidade do objeto, não o que vem dentro: preço por ml não faz sentido
CONTAINER = re.compile(
    r"banheira|\bpotes?\b|mamadeira|\bcopos?\b|garrafa|squeeze|recipiente|frasco|esterilizador|aquecedor|"
    r"\bbolsa|balde|ofur[ôo]|saquinho|sacos? para leite|armazenamento|porta[ -]|bomba|tira[ -]leite|"
    r"umidificador|vaporizador|garrafinha|\bkit\b|conjunto|organizador|dosador|prato|tigela",
    re.I,
)

INTROS = [
    "Olha só o que apareceu! 👀",
    "Achei uma oferta pra você! 🛍️",
    "Bora economizar? 💸",
    "Esse aqui merece uma olhada! ✨",
    "Oferta fresquinha por aqui! 🔥",
]


# dia de promoção (/promo 11.11, /promo black friday): o nome vai no topo e no fim da mensagem
PROMO_INTROS = [
    "Olha esse preço! 👀",
    "Bora aproveitar enquanto dura! 💸",
    "Desconto que vale a pena! ✨",
    "Essa saiu boa! 🛍️",
    "Corre que tá voando! 🏃",
]


def promo_emoji(name):
    low = name.lower()
    if "black" in low:
        return "🖤"
    if "11.11" in low:
        return "🛍️"
    return "🔥"


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


def product_caption(p, category, url, invite=None, promo=None):
    """Mensagem de um produto da fila. p: dict com title, price, units, size, seen_at.
    invite: link do grupo, para quem receber a mensagem encaminhada poder entrar.
    promo: nome da promoção do dia (ex.: '11.11'), que vira o cabeçalho da mensagem."""
    lines = []
    if promo:
        e = promo_emoji(promo)
        lines.append(f"{e} <b>{html.escape(promo.upper())}</b> {e}")
    if p.get("flash"):
        lines.append("⚡ <b>OFERTA RELÂMPAGO!</b> Corre que acaba rápido!")
    else:
        lines.append(random.choice(PROMO_INTROS if promo else INTROS))
    lines += ["", f"<b>{html.escape(p['title'][:90])}</b>", ""]
    if p.get("list_price"):
        lines.append(f"🏷️ De <s>{brl(p['list_price'])}</s> por <b>{brl(p['price'])}</b> ({p['discount']}% off)")
    else:
        lines.append(f"💰 {brl(p['price'])}")
    units = p.get("units")
    if units and units > 1:
        lines.append(f"📦 {brl(p['price'] / units)} por unidade ({units} un)")
    if p.get("size") and not CONTAINER.search(p.get("title", "")):
        amount, kind = p["size"]
        total = " no total" if units and units > 1 else ""
        lines.append(f"⚖️ {per_size(p['price'], amount, kind)} ({fmt_amount(amount, kind)}{total})")
    closing = (
        f"⏳ Preço de {html.escape(promo)}: pode acabar a qualquer momento!"
        if promo
        else "⏳ Corre que o preço pode mudar a qualquer momento!"
    )
    lines += ["", f"👉 {url}", "", closing]
    if invite:
        lines += ["", f"📲 Quer mais ofertas assim? Entre no grupo: {invite}"]
    return "\n".join(lines)
