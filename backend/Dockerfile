FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 berev \
    && useradd --system --uid 10001 --gid berev --home-dir /app berev \
    && mkdir -p /var/lib/berev/expense-receipts \
    && chown -R berev:berev /var/lib/berev/expense-receipts
WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --requirement requirements.txt

COPY --chown=berev:berev backend/app ./app
COPY --chown=berev:berev backend/berev_core ./berev_core
COPY --chown=berev:berev backend/alembic ./alembic
COPY --chown=berev:berev backend/alembic.ini ./alembic.ini
COPY --chown=berev:berev backend/scripts ./scripts

USER berev
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=\"$FORWARDED_ALLOW_IPS\""]
