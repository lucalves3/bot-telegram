# Bot (Python) + ponte WhatsApp (Node/Baileys) no mesmo container: o bot fala com a ponte em 127.0.0.1
FROM node:20-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY whatsapp/package.json whatsapp/package-lock.json whatsapp/
RUN cd whatsapp && npm ci --omit=dev

COPY . .

ENV PYTHONUNBUFFERED=1
CMD ["sh", "start.sh"]
