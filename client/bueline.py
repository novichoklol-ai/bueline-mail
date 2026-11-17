#!/usr/bin/env python3
"""BueLine Mail client — stdlib-only library + CLI.

Usage as a library:

    from bueline import Client
    c = Client("https://relay.example.com:4224", token="...", insecure=True)
    mid = c.send("bob", subject="task", body="do the thing", ref="issue-42")
    for m in c.inbox():
        print(m["id"], m["subject"], m["body"])
        c.ack([m["id"]], "read")

Usage as a CLI:

    export BUELINE_URL=https://127.0.0.1:4224
    export BUELINE_TOKEN=...
    python3 bueline.py send --to bob --subject hi --body "hello"
    python3 bueline.py inbox
    python3 bueline.py ack --ids 1 2 --state read
    python3 bueline.py status
    python3 bueline.py watch --interval 10        # poll inbox, auto-mark read
"""
import argparse
import json
import os
import ssl
import sys
import time
import urllib.request

STATES = ["delivered", "read", "in_work", "done"]


def default_token():
    t = os.environ.get("BUELINE_TOKEN", "").strip()
    if t:
        return t
    for p in (os.path.expanduser("~/.config/bueline/token"), "./.bueline-token"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                t = f.read().strip()
                if t:
                    return t
        except OSError:
            pass
    return ""


class Client:
    def __init__(self, base_url, token=None, insecure=False, cafile=None, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.token = token if token is not None else default_token()
        self.timeout = timeout
        if self.base_url.startswith("https"):
            if insecure:
                self.ctx = ssl._create_unverified_context()
            else:
                self.ctx = ssl.create_default_context(cafile=cafile)
        else:
            self.ctx = None

    def _req(self, method, path, payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as r:
            return json.loads(r.read().decode("utf-8"))

    # --- API ---------------------------------------------------------------
    def health(self):
        return self._req("GET", "/health")

    def boxes(self):
        return self._req("GET", "/v1/boxes").get("boxes", [])

    def send(self, to, subject="", body="", ref=""):
        r = self._req("POST", "/v1/send", {"to": to, "subject": subject, "body": body, "ref": ref})
        return r.get("id")

    def inbox(self, limit=50):
        return self._req("GET", "/v1/inbox?limit=%d" % limit).get("messages", [])

    def ack(self, ids, state):
        return self._req("POST", "/v1/ack", {"ids": list(ids), "state": state})

    def status(self):
        return self._req("GET", "/v1/status")


def main():
    ap = argparse.ArgumentParser(description="BueLine Mail client")
    ap.add_argument("--url", default=os.environ.get("BUELINE_URL", "https://127.0.0.1:4224"))
    ap.add_argument("--token", default=None)
    ap.add_argument("--insecure", action="store_true", help="skip TLS verification (self-signed certs)")
    ap.add_argument("--ca", default=None, help="pin this CA/cert file instead of system roots")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("send")
    p.add_argument("--to", required=True)
    p.add_argument("--subject", default="")
    p.add_argument("--body", default="")
    p.add_argument("--ref", default="")

    p = sub.add_parser("inbox")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("ack")
    p.add_argument("--ids", type=int, nargs="+", required=True)
    p.add_argument("--state", choices=STATES, required=True)

    sub.add_parser("status")
    sub.add_parser("health")
    sub.add_parser("boxes")

    p = sub.add_parser("watch", help="poll inbox and auto-ack as read")
    p.add_argument("--interval", type=int, default=10)

    args = ap.parse_args()
    c = Client(args.url, token=args.token, insecure=args.insecure, cafile=args.ca)
    if not c.token and args.cmd != "health":
        ap.error("no token: set BUELINE_TOKEN, pass --token, or write ~/.config/bueline/token")

    if args.cmd == "send":
        print(json.dumps({"id": c.send(args.to, args.subject, args.body, args.ref)}, ensure_ascii=False))
    elif args.cmd == "inbox":
        print(json.dumps(c.inbox(args.limit), ensure_ascii=False, indent=2))
    elif args.cmd == "ack":
        print(json.dumps(c.ack(args.ids, args.state), ensure_ascii=False))
    elif args.cmd == "status":
        print(json.dumps(c.status(), ensure_ascii=False, indent=2))
    elif args.cmd == "health":
        print(json.dumps(c.health(), ensure_ascii=False))
    elif args.cmd == "boxes":
        print(json.dumps(c.boxes(), ensure_ascii=False))
    elif args.cmd == "watch":
        while True:
            msgs = c.inbox()
            for m in msgs:
                print("[#%d] %s -> %s | %s | %s" % (m["id"], m["frm"], m["to_box"], m["subject"], m["body"][:200]))
                try:
                    c.ack([m["id"]], "read")
                except Exception as e:
                    print("ack failed for #%d: %s" % (m["id"], e), file=sys.stderr)
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
