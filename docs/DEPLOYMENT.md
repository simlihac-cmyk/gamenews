# Deployment Workflow

This workspace is the development repo. The deployment repo can be a separate GitHub repository, for example:

```text
git@github.com:simlihac-cmyk/gamenews.git
```

Recommended remote layout:

```bash
git remote add deploy git@github.com:simlihac-cmyk/gamenews.git
git push deploy main
```

For later releases:

```bash
git status
git add .
git commit -m "Describe the release"
./scripts/deploy_push.sh deploy main
```

The deployment repo receives only committed release snapshots. Keep experimental work in the development repo until it is ready.

## Production Compose

Use the production compose file for the Mac mini service:

```bash
cp .env.production.example .env
docker compose -f docker-compose.prod.yml up --build -d
docker compose -f docker-compose.prod.yml exec web python manage.py migrate
docker compose -f docker-compose.prod.yml exec web python manage.py collectstatic --noinput
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
docker compose -f docker-compose.prod.yml exec web python manage.py seed_sources
```

The production compose file uses gunicorn and `restart: unless-stopped`.

## Production Domain

Nintendo Watch is configured for:

```text
https://gamenews.monosaccharide180.com/
```

Production `.env` should include:

```env
ALLOWED_HOSTS=gamenews.monosaccharide180.com
CSRF_TRUSTED_ORIGINS=https://gamenews.monosaccharide180.com
SECURE_SSL_REDIRECT=true
SESSION_COOKIE_SECURE=true
CSRF_COOKIE_SECURE=true
USE_X_FORWARDED_PROTO=true
USE_X_FORWARDED_HOST=true
```

Run gunicorn behind a TLS-terminating reverse proxy such as Caddy, nginx, Traefik, or Cloudflare Tunnel. The proxy should pass:

```text
Host: gamenews.monosaccharide180.com
X-Forwarded-Proto: https
```

Point the domain or tunnel to the Mac mini service on port `8000`.

## Scheduled Fetch

Use launchd on macOS:

```bash
cp deploy/launchd/com.nintendowatch.fetch.plist.example ~/Library/LaunchAgents/com.nintendowatch.fetch.plist
launchctl load ~/Library/LaunchAgents/com.nintendowatch.fetch.plist
```

Edit the plist path first if your checkout directory is different.

## Backups

Create a PostgreSQL dump:

```bash
./scripts/backup_postgres.sh
```

Keep the `backups/` directory out of Git.
