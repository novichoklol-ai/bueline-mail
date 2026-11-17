#!/usr/bin/env python3
"""BueLine Mail — async mailbox relay for AI agents (and humans).

Design goals:
- stdlib only (http.server + sqlite3 + ssl), no pip installs;
- one process, one SQLite file, systemd-friendly;
- fixed message states: new -> delivered -> read -> in_work -> done (monotonic);
- delivery receipts and stale-message detection built in;
- optional TLS + per-box bearer tokens; tokens come from env, never from git.

Configuration (environment):
    MAILRELAY_TOKEN_<BOX>   one variable per mailbox, e.g. MAILRELAY_TOKEN_ALICE=...
                            (box names are lower-cased; at least 2 boxes required)
    MAILRELAY_STALE_MINUTES minutes before an un-acked message is reported stale (default 45)
    MAILRELAY_MAX_BODY      max message body in bytes (default 65536)
"""
import argparse
import datetime
import json
import logging
import os
import sqlite3
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

VERSION = "1.0.0"
STATES = ["new", "delivered", "read", "in_work", "done"]
STATE_IDX = {s: i for i, s in enumerate(STATES)}
TOKEN_PREFIX = "MAILRELAY_TOKEN_"

log = logging.getLogger("mailrelay")


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    frm TEXT NOT NULL,
                    to_box TEXT NOT NULL,
                    subject TEXT DEFAULT '',
                    body TEXT DEFAULT '',
                    ref TEXT DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'new',
                    state_ts TEXT NOT NULL)"""
            )
            self.db.commit()

    def send(self, frm, to_box, subject, body, ref):
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO messages(ts, frm, to_box, subject, body, ref, state, state_ts) VALUES(?,?,?,?,?,?,'new',?)",
                (utcnow(), frm, to_box, subject, body, ref, utcnow()),
            )
            self.db.commit()
            return cur.lastrowid

    def fetch_new(self, box, limit):
        """Return new messages for box and advance them to 'delivered'."""
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM messages WHERE to_box=? AND state='new' ORDER BY id LIMIT ?",
                (box, limit),
            ).fetchall()
            now = utcnow()
            out = []
            for r in rows:
                self.db.execute("UPDATE messages SET state='delivered', state_ts=? WHERE id=?", (now, r["id"]))
                d = dict(r)
                d["state"] = "delivered"
                d["state_ts"] = now
                out.append(d)
            self.db.commit()
            return out

    def ack(self, box, ids, state):
        """Advance own incoming messages to a later state. Returns (updated, skipped)."""
        updated, skipped = [], []
        with self._lock:
            for mid in ids:
                row = self.db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
                if not row or row["to_box"] != box:
                    skipped.append(mid)
                    continue
                if STATE_IDX[state] <= STATE_IDX[row["state"]]:
                    skipped.append(mid)
                    continue
                self.db.execute("UPDATE messages SET state=?, state_ts=? WHERE id=?", (state, utcnow(), mid))
                updated.append(mid)
            self.db.commit()
        return updated, skipped

    def status(self, box, stale_minutes):
        with self._lock:
            counts = {s: 0 for s in STATES}
            for r in self.db.execute("SELECT state, COUNT(*) c FROM messages WHERE to_box=? GROUP BY state", (box,)):
                counts[r["state"]] = r["c"]
            cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=stale_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
            stale = [dict(r) for r in self.db.execute(
                "SELECT id, ts, frm, subject, state, state_ts FROM messages WHERE to_box=? AND state IN ('new','delivered') AND state_ts < ? ORDER BY id",
                (box, cutoff),
            ).fetchall()]
        return {"box": box, "counts": counts, "stale_minutes": stale_minutes, "stale": stale}


def load_tokens():
    """Per-box tokens from environment: MAILRELAY_TOKEN_<BOX>. Box names are lower-cased."""
    tokens = {}
    for k, v in os.environ.items():
        if k.startswith(TOKEN_PREFIX):
            box = k[len(TOKEN_PREFIX):].strip().lower()
            t = (v or "").strip()
            if box and t:
                tokens[t] = box
    return tokens


class Handler(BaseHTTPRequestHandler):
    server_version = "BueLineMail/" + VERSION

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    # --- helpers -----------------------------------------------------------
    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if n > self.server.max_body + 4096:
            return None
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _box(self, params):
        token = ""
        if isinstance(params, dict):
            v = params.get("token", "")
            token = (v or [""])[0] if isinstance(v, list) else (v or "")
        if not token:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:].strip()
        return self.server.tokens.get(token)

    # --- routes ------------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            self._json(200, {"ok": True, "service": "bueline-mail", "version": VERSION, "ts": utcnow()})
            return
        box = self._box(q)
        if not box:
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        if u.path == "/v1/inbox":
            limit = int(q.get("limit", ["50"])[0])
            msgs = self.server.store.fetch_new(box, min(limit, 100))
            self._json(200, {"ok": True, "box": box, "count": len(msgs), "messages": msgs})
        elif u.path == "/v1/status":
            self._json(200, {"ok": True, **self.server.store.status(box, self.server.stale_minutes)})
        elif u.path == "/v1/boxes":
            self._json(200, {"ok": True, "boxes": sorted(set(self.server.tokens.values()))})
        else:
            self._json(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        u = urlparse(self.path)
        body = self._body()
        if not isinstance(body, dict):
            self._json(400, {"ok": False, "error": "bad_json"})
            return
        box = self._box(body)
        if not box:
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        if u.path == "/v1/send":
            boxes = set(self.server.tokens.values())
            to_box = str(body.get("to", "")).strip().lower()
            if to_box not in boxes:
                self._json(400, {"ok": False, "error": "bad_recipient", "boxes": sorted(boxes)})
                return
            text = str(body.get("body", ""))
            if len(text.encode("utf-8")) > self.server.max_body:
                self._json(413, {"ok": False, "error": "too_large", "max": self.server.max_body})
                return
            mid = self.server.store.send(
                frm=box, to_box=to_box,
                subject=str(body.get("subject", ""))[:200],
                body=text, ref=str(body.get("ref", ""))[:200],
            )
            log.info("msg %s: %s -> %s", mid, box, to_box)
            self._json(200, {"ok": True, "id": mid, "state": "new"})
        elif u.path == "/v1/ack":
            state = str(body.get("state", "")).strip()
            if state not in STATE_IDX or state == "new":
                self._json(400, {"ok": False, "error": "bad_state", "states": STATES[1:]})
                return
            ids = body.get("ids") or []
            if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
                self._json(400, {"ok": False, "error": "bad_ids"})
                return
            updated, skipped = self.server.store.ack(box, ids[:200], state)
            self._json(200, {"ok": True, "updated": updated, "skipped": skipped, "state": state})
        else:
            self._json(404, {"ok": False, "error": "not_found"})


def main():
    ap = argparse.ArgumentParser(description="BueLine Mail — async mailbox relay for AI agents")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4224)
    ap.add_argument("--db", default="./mailrelay.db")
    ap.add_argument("--cert", default="", help="TLS certificate chain file (PEM); omit for plain HTTP")
    ap.add_argument("--key", default="", help="TLS private key file (PEM)")
    ap.add_argument("--stale-minutes", type=int, default=env_int("MAILRELAY_STALE_MINUTES", 45))
    ap.add_argument("--max-body", type=int, default=env_int("MAILRELAY_MAX_BODY", 65536))
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    tokens = load_tokens()
    if len(tokens) < 2:
        log.error("need at least 2 tokens in env (MAILRELAY_TOKEN_<BOX>=...)")
        sys.exit(2)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.store = Store(args.db)
    srv.tokens = tokens
    srv.stale_minutes = args.stale_minutes
    srv.max_body = args.max_body
    srv.daemon_threads = True

    scheme = "http"
    if args.cert and args.key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(args.cert, args.key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        scheme = "https"
        log.info("TLS enabled")
    log.info("BueLine Mail %s listening on %s://%s:%s, db=%s, boxes=%s",
             VERSION, scheme, args.host, args.port, args.db, sorted(set(tokens.values())))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
