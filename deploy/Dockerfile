FROM python:3.12-slim

WORKDIR /app
COPY bueline_mail.py /app/

RUN useradd --system --home /data bueline && mkdir -p /data && chown bueline /data
USER bueline
VOLUME /data
EXPOSE 4224

# Tokens are injected via environment (see docker-compose.yml).
# TLS: mount certs to /data and pass --cert/--key.
ENTRYPOINT ["python3", "/app/bueline_mail.py"]
CMD ["--host", "0.0.0.0", "--port", "4224", "--db", "/data/mailrelay.db"]
