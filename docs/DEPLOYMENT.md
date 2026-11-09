# Production deployment

This deployment targets a Linux host with Docker Engine and the Docker Compose plugin. It serves the fixed hostname `erp.berev.com` through Traefik, which obtains TLS certificates from Let's Encrypt. Point the domain's DNS records at the host and allow inbound TCP ports 80 and 443 before starting.

## Configure secrets

From the repository root, create a private environment file and generate fresh URL-safe secrets:

```sh
cp .env.example .env
chmod 600 .env
openssl rand -hex 32
```

Set distinct generated values for `POSTGRES_ADMIN_PASSWORD`, `BEREV_APP_PASSWORD`, and `JWT_SECRET_KEY` in `.env`. Hex values avoid URL-encoding issues in the database connection string. Set `ACME_EMAIL` to an address that receives certificate-expiry notices. Do not commit `.env` or use credentials that appeared in prior repository history.

The hostname is configured in `docker-compose.prod.yml` and `deploy/traefik/dynamic/routers.yml`. If changing it, update both files and the `PUBLIC_APP_URL` and `CORS_ALLOWED_ORIGINS` values together.
The Compose networks use `172.30.128.0/24` through `172.30.130.0/24`, with Traefik at `172.30.129.2`; check for address conflicts on the host before startup. If changing the Traefik address, update `TRAEFIK_IP` in `.env`; Uvicorn trusts only that proxy IP for forwarded headers.

## Start the application

The backup container runs as UID/GID 10001. Create a private, writable backup directory for it:

```sh
install -d -o 10001 -g 10001 -m 0700 ./backups
docker compose -f docker-compose.prod.yml config -q
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec api python -m app.cli create-admin
curl --fail https://erp.berev.com/api/v1/health/ready
```

The API and database have no published host ports. Traefik applies HTTPS, browser security headers, a per-IP average limit of 120 API requests per minute (burst 20), and a per-IP average limit of 10 login requests per minute (burst 3). The login router has higher priority than the general API router.

This configuration assumes clients connect directly to Traefik. If another proxy or load balancer is placed in front, configure and test Traefik's trusted forwarded-header/IP strategy first; otherwise rate limits may apply to the upstream proxy address rather than individual clients.

## Backups and restore drill

The backup service immediately creates a PostgreSQL custom-format dump and a compressed archive of the expense-receipts volume, then repeats at `BACKUP_INTERVAL_SECONDS` (default: daily). It keeps files for `BACKUP_RETENTION_DAYS` (default: 14). A `.complete` marker is written only after both archives are complete. Check `docker compose -f docker-compose.prod.yml logs backup` and monitor the backup directory and free disk space.

These backups are stored locally and are **not encrypted or copied off-host**. Configure encrypted, access-controlled off-host replication and test it before relying on this setup for accounting records. Database dumps and receipt archives contain sensitive customer and financial data. Keep the backup path on durable storage, restrict access, and include the backup copy in your organization's retention policy.

The restore drill creates a temporary PostgreSQL container on a separate Compose network; it has no connection to the live database and no persistent volume. It restores the newest complete backup set, checks the database schema and receipt archive, and then runs the application's preflight and accounting-integrity checks:

```sh
docker compose -f docker-compose.prod.yml --profile restore build restore-check
docker compose -f docker-compose.prod.yml --profile restore up -d restore-db
docker compose -f docker-compose.prod.yml --profile restore run --rm --no-deps restore-drill
docker compose -f docker-compose.prod.yml --profile restore run --rm --no-deps restore-check
docker compose -f docker-compose.prod.yml --profile restore rm -sf restore-db
```

Run this drill periodically and retain its output. It does not replace off-host backup replication or a disaster-recovery exercise on separate infrastructure.

## Updates and remaining operational work

Before deploying an update, confirm a recent complete backup. Rebuild and restart with `docker compose -f docker-compose.prod.yml up -d --build`; the API applies Alembic migrations during startup. Downgrades are not a general rollback plan—use a rehearsed restore procedure instead.

The CI workflow validates Compose syntax, shell syntax, and container builds. Docker is unavailable in the current development environment, so local container builds, HTTPS issuance, backup execution, and the restore drill have not been run here. A production operator must verify those on a disposable host/database before accepting real data.
