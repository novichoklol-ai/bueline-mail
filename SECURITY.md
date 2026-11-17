# Security Policy

BueLine Mail is designed for small, trusted fleets of agents behind TLS + per-box bearer tokens.

- Never commit tokens, certificates, or private keys. Tokens live in environment files with mode 600.
- Never send secrets (passwords, API keys, account numbers) inside messages — the relay does not scan payloads.
- The server binds to 127.0.0.1 by default; expose it only behind TLS (`--cert/--key`) and a firewall.

Report vulnerabilities via GitHub issues or privately to the repository owner.
