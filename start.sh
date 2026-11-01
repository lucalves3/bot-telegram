#!/bin/sh
# Sobe a ponte WhatsApp (se ligada) em segundo plano e o bot em primeiro plano.
set -e

if [ "$WHATSAPP_ENABLED" = "1" ] || [ -n "$WHATSAPP_GROUP_ID" ]; then
  (
    cd whatsapp
    # se a ponte cair, sobe de novo
    while true; do
      node index.js || true
      echo "[start] ponte WhatsApp parou; reiniciando em 5s"
      sleep 5
    done
  ) &
fi

exec python3 main.py run
