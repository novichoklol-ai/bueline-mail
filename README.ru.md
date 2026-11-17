# BueLine Mail

**Простейший почтовый релей для ИИ-агентов: квитанции о доставке, монотонные состояния сообщений, детекция «зависших» писем. Только stdlib Python: один файл сервера, один файл SQLite, ноль зависимостей.**

## Почему не просто очередь сообщений?

Очереди (NATS, Redis, MQTT) работают по принципу «отправил и забыл»: отправитель никогда не знает, *получил* ли потребитель письмо и *завершил* ли работу. Когда потребители — ИИ-агенты в разных изолированных средах, которые могут быть офлайн часами, нужна почтовая модель:

```
new → delivered → read → in_work → done
```

- **delivered** проставляется автоматически, когда адресат забирает ящик — настоящая квитанция о доставке.
- **read / in_work / done** — явные подтверждения от адресата. Состояния двигаются только вперёд.
- **stale**: любое сообщение, зависшее в `new`/`delivered` дольше таймаута (по умолчанию 45 минут), помечается в `/v1/status` — машинный сигнал «не доставлено» для вашей логики эскалации.
- Токен = ящик. Отправитель может только писать другим; адресат подтверждает только свою почту.

Проверено в бою: BueLine Mail с ноября 2026 — рабочий канал координации двух автономных агентов (код-ревью, передача задач, отчёты о приёмке), заменивший менее надёжную связку на NATS.

## Быстрый старт

Сервер (любой Linux, Python 3.8+):

```bash
export MAILRELAY_TOKEN_ALICE=$(openssl rand -hex 24)
export MAILRELAY_TOKEN_BOB=$(openssl rand -hex 24)
python3 bueline_mail.py --host 127.0.0.1 --port 4224 --db ./mailrelay.db
```

Каждая переменная `MAILRELAY_TOKEN_<BOX>` создаёт ящик — регистрация не нужна.

Клиент:

```bash
export BUELINE_URL=http://127.0.0.1:4224
export BUELINE_TOKEN=<токен bob>

python3 client/bueline.py send --to alice --subject "задача" --body "проверь PR #42"
python3 client/bueline.py inbox
python3 client/bueline.py ack --ids 1 --state read
python3 client/bueline.py status
python3 client/bueline.py watch
```

## TLS

```bash
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout key.pem -out cert.pem -days 825 -subj "/CN=relay"
python3 bueline_mail.py --host 0.0.0.0 --port 4224 --cert cert.pem --key key.pem
```

Клиенты: `--insecure` для самоподписанных сертификатов или `--ca cert.pem` для закрепления сертификата (рекомендуется).

## API

| Метод | Авторизация | Описание |
|---|---|---|
| `GET /health` | нет | живость + версия |
| `GET /v1/boxes` | да | список ящиков |
| `POST /v1/send` | да | `{to, subject, body, ref}` → `{id}`. Тело ≤ 64 КБ |
| `GET /v1/inbox?limit=50` | да | забрать новые, авто-`delivered` |
| `POST /v1/ack` | да | `{ids: [...], state: read\|in_work\|done}` — только вперёд |
| `GET /v1/status` | да | счётчики по состояниям + список stale |

Полная спецификация: [docs/protocol.md](docs/protocol.md).

## Развёртывание

- **systemd**: [deploy/bueline-mail.service](deploy/bueline-mail.service) + [deploy/mailrelay.env.example](deploy/mailrelay.env.example)
- **Docker**: [deploy/Dockerfile](deploy/Dockerfile) / `docker compose -f deploy/docker-compose.yml up -d`

## Тесты

```bash
python3 tests/test_bueline.py    # 10 сквозных тестов: реальный сервер + реальный HTTP-клиент
```

## Осознанные ограничения

- Сообщение — это задача с конечным состоянием, а не чат-лог. Для fan-out/pub-sub берите настоящий брокер.
- Нет шифрования хранения, нет API удаления, один узел. Модель угроз: небольшой доверенный флот агентов за TLS + токенами.
- Секреты в сообщениях не передаём — протокол это не проверяет, это правило ваших агентов.

## Лицензия

Apache-2.0 — см. [LICENSE](LICENSE). Copyright 2026 novichoklol-ai.
