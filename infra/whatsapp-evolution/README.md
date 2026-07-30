# WhatsApp via Evolution API

Runs the server the `whatsapp_evolution` connector talks to. WhatsApp has no official
API for a personal number, so this speaks the unofficial multi-device protocol —
**against Meta's terms, and the number can be banned.** Use a spare SIM, never the one
you depend on.

## Setup

```sh
cd infra/whatsapp-evolution
cp .env.example .env

# Append both secrets — the file starts with them EMPTY, and compose would come up
# with an unauthenticated API and a passwordless database.
python3 -c "import secrets; print('AUTHENTICATION_API_KEY=' + secrets.token_urlsafe(32))" >> .env
python3 -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(24))" >> .env

docker compose up -d
```

(The appended lines win over the empty ones above them — later assignments override
earlier ones in a `.env`. Delete the blank pair if you prefer a tidy file.)

Then open `http://localhost:8090/manager`, paste the API key, create an instance named
`openworker`, and pair it: **WhatsApp → Settings → Linked devices → Link a device**.

In Judith: Settings ▸ Connectors ▸ enable experimental connectors, then connect
**WhatsApp (self-hosted)** with the server URL, the API key, and the instance name.

## Two things that are easy to get wrong

**`extra_hosts` is not optional.** Inside a container `127.0.0.1` is the container, so a
webhook aimed at loopback dies with `ECONNREFUSED` — the connector looks connected and
receives nothing. The compose maps `host.docker.internal` to the host, which is where
the sidecar listens.

**The webhook is per-instance, not global.** `WEBHOOK_GLOBAL_ENABLED` stays `false`
because the desktop app assigns the sidecar a fresh port each boot; the connector
re-registers the current URL on every connect, and disables the previous one so
Evolution's retry queue doesn't fill with dead deliveries.

## Everything is loopback

All three services bind `127.0.0.1`. Nothing on your network can reach an API that
controls a WhatsApp account. Do not "fix" this by binding `0.0.0.0` — put it behind a
tunnel or a reverse proxy with its own auth if you need remote access.

## If messages stop arriving

The instance can report `state: open` while its socket is half-dead — sends work,
receives don't. In order of cost:

```sh
# 1. reconnect the WhatsApp socket
curl -X POST localhost:8090/instance/restart/openworker -H "apikey: $KEY"

# 2. clear a poisoned retry queue (harmless: it is a cache)
docker exec evolution-redis redis-cli FLUSHDB && docker compose restart evolution

# 3. check where the webhook currently points
curl localhost:8090/webhook/find/openworker -H "apikey: $KEY"
```

If the URL there is not the sidecar's current port, reconnect the connector in
Judith — that is what re-registers it.

## Data

Conversations and the pairing live in Docker volumes (`evolution_instances`,
`evolution_pgdata`). `docker compose down` keeps them; `down -v` deletes them and you
re-pair with a new QR.
