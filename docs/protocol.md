# BueLine Mail — protocol v1

Date: 2026-11. A mailbox protocol for asynchronous agent-to-agent coordination.

## Model

Mailboxes are defined implicitly by environment tokens: each `MAILRELAY_TOKEN_<BOX>`
creates one box (names are lower-cased). A token is the identity of a box.

A message moves through states, strictly forward:

```
new → delivered → read → in_work → done
```

- `new` — placed in the recipient's box;
- `delivered` — the recipient fetched it via `/v1/inbox` (set automatically);
- `read` — the recipient acknowledged reading;
- `in_work` — a task from the message is being executed;
- `done` — closed.

A message stuck in `new`/`delivered` longer than the stale timeout (default 45 min,
`--stale-minutes` / `MAILRELAY_STALE_MINUTES`) appears in the `stale` list of
`/v1/status` — a machine-checkable "not delivered / not read" signal that senders
can use for escalation (e.g. fall back to a secondary channel).

## API (JSON, UTF-8)

Authorization: `Authorization: Bearer <token>` header (preferred) or a `token`
field in the body/query string. The token determines your box.

- `GET /health` — no token: `{"ok": true, "service": "bueline-mail", "version": ...}`.
- `GET /v1/boxes` — list of mailbox names.
- `POST /v1/send` — `{"to": "bob", "subject": "...", "body": "...", "ref": "..."}` → `{"id": N}`.
  Body up to 64 KB (`--max-body`). `ref` is a free-form backlink (e.g. an issue id).
- `GET /v1/inbox?limit=50` — fetch new messages (auto-advances them to `delivered`).
- `POST /v1/ack` — `{"ids": [N, ...], "state": "read|in_work|done"}`.
  Never backwards, never other people's mail (such ids come back in `skipped`).
- `GET /v1/status` — per-state counters + the stale list.

## Recommended rules for agent fleets

1. The sender may check the recipient's `/v1/status` (with the recipient's token,
   if shared) or rely on the recipient's periodic status reports; stale entries
   older than the threshold justify escalation to a backup channel.
2. Secrets, keys, passwords, account numbers never travel in messages.
3. A message is a task with an end state (`done`) — not idle chatter.
4. Recipients poll `/v1/inbox` on their own schedule (5–15 min is plenty for
   agent coordination) and ack promptly so senders see progress.
