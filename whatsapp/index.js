// Ponte WhatsApp (Baileys) para o Price Hunter.
// Expõe uma API HTTP local que o main.py usa:
//   GET  /status   -> { connected }
//   GET  /groups   -> [{ id, subject }]   (para achar o WHATSAPP_GROUP_ID)
//   GET  /updates  -> mensagens de texto recebidas desde a última chamada
//   POST /send     -> { jid, text, image? }
import http from "node:http";
import makeWASocket, { DisconnectReason, useMultiFileAuthState, fetchLatestBaileysVersion } from "@whiskeysockets/baileys";
import pino from "pino";
import qrcode from "qrcode-terminal";

const PORT = Number(process.env.WHATSAPP_BRIDGE_PORT || 3001);
const AUTH_DIR = new URL("./auth/", import.meta.url).pathname.replace(/^\/(\w:)/, "$1");

let sock = null;
let connected = false;
const inbox = [];

function textOf(m) {
  return m?.conversation || m?.extendedTextMessage?.text || m?.imageMessage?.caption || "";
}

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();
  sock = makeWASocket({ version, auth: state, logger: pino({ level: "warn" }), markOnlineOnConnect: false });
  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      console.log("Escaneie o QR no WhatsApp > Aparelhos conectados:");
      qrcode.generate(qr, { small: true });
    }
    if (connection === "open") {
      connected = true;
      console.log("WhatsApp conectado.");
    }
    if (connection === "close") {
      connected = false;
      const code = lastDisconnect?.error?.output?.statusCode;
      if (code === DisconnectReason.loggedOut) {
        console.log("Sessão encerrada. Apague a pasta whatsapp/auth e escaneie de novo.");
      } else {
        console.log(`Conexão caiu (${code}), reconectando...`);
        setTimeout(start, 3000);
      }
    }
  });

  sock.ev.on("messages.upsert", ({ messages, type }) => {
    if (type !== "notify") return;
    for (const msg of messages) {
      const text = textOf(msg.message);
      if (!text) continue;
      const k = msg.key;
      inbox.push({
        jid: k.remoteJid,
        // número de quem mandou (em grupo é o participant; *Pn/*Alt quando o WhatsApp usa LID)
        sender: k.participantPn || k.participantAlt || k.participant || k.senderPn || k.remoteJid,
        from_me: Boolean(k.fromMe),
        text,
      });
      if (inbox.length > 500) inbox.shift();
    }
  });
}

function json(res, code, body) {
  res.writeHead(code, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (c) => (data += c));
    req.on("end", () => {
      try {
        resolve(data ? JSON.parse(data) : {});
      } catch (e) {
        reject(e);
      }
    });
  });
}

http
  .createServer(async (req, res) => {
    try {
      if (req.method === "GET" && req.url === "/status") return json(res, 200, { connected });
      if (!connected) return json(res, 503, { error: "WhatsApp desconectado" });
      if (req.method === "GET" && req.url === "/updates") return json(res, 200, inbox.splice(0));
      if (req.method === "GET" && req.url === "/groups") {
        const groups = await sock.groupFetchAllParticipating();
        return json(res, 200, Object.values(groups).map((g) => ({ id: g.id, subject: g.subject })));
      }
      if (req.method === "POST" && req.url === "/send") {
        const { jid, text, image } = await readBody(req);
        const content = image ? { image: { url: image }, caption: text } : { text };
        const sent = await sock.sendMessage(jid, content);
        return json(res, 200, { id: sent?.key?.id });
      }
      json(res, 404, { error: "não encontrado" });
    } catch (e) {
      console.error("[bridge]", e);
      json(res, 500, { error: String(e?.message || e) });
    }
  })
  .listen(PORT, "127.0.0.1", () => console.log(`Ponte WhatsApp em http://127.0.0.1:${PORT}`));

start();
