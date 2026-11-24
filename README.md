# BueLine Mail

[![tests](https://github.com/novichoklol-ai/bueline-mail/actions/workflows/tests.yml/badge.svg)](https://github.com/novichoklol-ai/bueline-mail/actions/workflows/tests.yml)

**Dead-simple mailbox relay for AI agents — with delivery receipts, monotonic message states, and stale-message detection. stdlib-only Python: one server file, one SQLite file, zero dependencies.**

[Русская версия README → README.ru.md](README.ru.md)

## Why not just a message queue?

Queues (NATS, Redis, MQTT) are fire-and-forget: a producer never really knows the consumer *got* the message and *finished* the work. When your "consumers" are AI agents running in separate sandboxes — sometimes offline for hours — you need mailbox semantics instead:

```
new → delivered → read → in_work → done
```

- **delivered** is set automatically when the recipient fetches the inbox — a real delivery receipt.
- **read / in_work / done** are explicit acknowledgements from the recipient. States only move forward.
- **stale**: any message stuck in `new`/`delivered` longer than a timeout (default 45 min) is flagged in `/v1/status` — a machine-checkable "this was not picked up" signal for your escalation logic.
- Per-box bearer tokens: a token *is* the mailbox identity. Senders can only write *to* others; recipients can only ack *their own* mail.

Born in production: BueLine Mail has been the coordination channel between two autonomous agents (code review, task handoff, acceptance reports) since Nov 2026, replacing a flakier NATS-based setup.

## Quickstart

Server (any Linux box, Python 3.8+):

```bash
export MAILRELAY_TOKEN_ALICE=$(openssl rand -hex 24)
export MAILRELAY_TOKEN_BOB=$(openssl rand -hex 24)
python3 bueline_mail.py --host 127.0.0.1 --port 4224 --db ./mailrelay.db
```

Each `MAILRELAY_TOKEN_<BOX>` env var defines a mailbox. Boxes are created implicitly — no registration step.

Client:

```bash
export BUELINE_URL=http://127.0.0.1:4224
export BUELINE_TOKEN=<bob's token>

python3 client/bueline.py send --to alice --subject "task" --body "review PR #42" --ref "pr-42"
python3 client/bueline.py inbox          # fetches new mail, marks it delivered
python3 client/bueline.py ack --ids 1 --state read
python3 client/bueline.py status         # counts by state + stale list
python3 client/bueline.py watch          # poll loop, auto-ack as read
```

Or as a library (stdlib only, works in restricted sandboxes):

```python
from client.bueline import Client
c = Client("https://relay:4224", token="...", insecure=True)  # self-signed TLS
c.send("alice", subject="report", body="done, see artifact X")
for m in c.inbox():
    process(m)
    c.ack([m["id"]], "done")
```

## TLS

```bash
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout key.pem -out cert.pem -days 825 -subj "/CN=relay"
python3 bueline_mail.py --host 0.0.0.0 --port 4224 --cert cert.pem --key key.pem
```

Clients use `--insecure` for self-signed certs or `--ca cert.pem` to pin the certificate (recommended).

## API

| Endpoint | Auth | Description |
|---|---|---|
| `GET /health` | no | liveness + version |
| `GET /v1/boxes` | yes | list mailbox names |
| `POST /v1/send` | yes | `{to, subject, body, ref}` → `{id}`. Body ≤ 64 KB |
| `GET /v1/inbox?limit=50` | yes | fetch new messages, auto-marks them `delivered` |
| `POST /v1/ack` | yes | `{ids: [...], state: read\|in_work\|done}` — forward-only |
| `GET /v1/status` | yes | per-state counters + stale messages |

Auth: `Authorization: Bearer <token>` header (or `token` field in body/query). Full spec: [docs/protocol.md](docs/protocol.md).

## Deploy

- **systemd**: [deploy/bueline-mail.service](deploy/bueline-mail.service) + [deploy/mailrelay.env.example](deploy/mailrelay.env.example)
- **Docker**: [deploy/Dockerfile](deploy/Dockerfile) / `docker compose -f deploy/docker-compose.yml up -d`

## Tests

```bash
python3 tests/test_bueline.py    # 10 end-to-end tests: real server thread + real HTTP client
```

## Design constraints (intentional)

- Messages are tasks with an end state, not a chat log. If you need pub/sub fan-out or streams, use a real broker.
- No at-rest encryption, no message deletion API, single-node. Threat model: small trusted agent fleets behind TLS + tokens.
- Secrets must never travel in messages — the protocol can't enforce that, your agent rules must.

## License

Apache-2.0 — see [LICENSE](LICENSE). Copyright 2026 novichoklol-ai.
