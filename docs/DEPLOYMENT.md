# Deployment Workflow

This workspace is the development repo. The deployment repo can be a separate GitHub repository, for example:

```text
https://github.com/simlihac-cmyk/gamenews.git
```

Recommended remote layout:

```bash
git remote add deploy https://github.com/simlihac-cmyk/gamenews.git
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
