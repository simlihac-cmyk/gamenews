# Nintendo Watch

Private Django web archive for Nintendo official news, media reports, rumors, leaks, trailers, Direct posts, and release schedule changes.

## Quick Start

```bash
cp .env.example .env
docker compose up --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py seed_sources
docker compose exec web python manage.py fetch_news --limit 20
```

Open the app at:

```text
http://localhost:8000/
```

Admin:

```text
http://localhost:8000/admin/
```

The private web UI requires login. Create an account before browsing:

```bash
docker compose exec web python manage.py createsuperuser
```

## Manual Commands

Seed default sources and franchises:

```bash
docker compose exec web python manage.py seed_sources
```

Fetch enabled sources:

```bash
docker compose exec web python manage.py fetch_news --limit 20
```

Fetch one source:

```bash
docker compose exec web python manage.py fetch_news --source gematsu --limit 20
```

Dry run:

```bash
docker compose exec web python manage.py fetch_news --limit 20 --dry-run
```

Recalculate existing items:

```bash
docker compose exec web python manage.py recalculate_items
```

Run tests:

```bash
docker compose exec web python manage.py test
```

## Notifications

Notifications are disabled by default. Set these in `.env`:

```env
NOTIFICATIONS_ENABLED=true
NOTIFICATION_MIN_IMPORTANCE=80
NTFY_TOPIC=your-topic
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

Then run:

```bash
docker compose exec web python manage.py fetch_news --limit 20 --notify
```

Notification behavior:

- Sends only when `NOTIFICATIONS_ENABLED=true`.
- Sends only when `importance_score >= NOTIFICATION_MIN_IMPORTANCE`.
- Records every send attempt in the `Notification` table.
- Prevents duplicate successful sends per item/channel.
- Failed sends are recorded and can be retried on a later fetch run.
- Korean messages include title, trust label, category, importance score, summary, and original URL.

## Running 24/7

For a Mac mini, run Docker Compose continuously and schedule fetches with cron or launchd. A simple cron-style command is:

```bash
docker compose exec -T web python manage.py fetch_news --limit 30 --notify
```

The fetch command handles broken sources gracefully and stores the error on the Source record.

For production-style Mac mini operation, see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). It includes:

- separate development/deployment Git repo workflow
- production Docker Compose with gunicorn
- launchd fetch example
- PostgreSQL backup script

## Development vs Deployment Repositories

This checkout can stay as the development repo. Add a separate GitHub repo as a deployment remote:

```bash
git remote add deploy https://github.com/simlihac-cmyk/gamenews.git
git push deploy main
```

After future release commits:

```bash
./scripts/deploy_push.sh deploy main
```

This keeps experimental commits local/dev until you intentionally push a release snapshot to the deployment repo.

## Source Health

Each fetch updates these fields on `Source`:

- `last_checked_at`: when the fetch attempt started.
- `last_success_at`: when the source completed without a top-level fetch failure.
- `last_error`: HTTP, parsing, or per-item errors from the latest run.
- `last_new_items_count`: number of newly stored `RawItem` records from the latest run.

The `/sources/health/` page shows those values alongside total raw/news item counts.

## HTML Source.config Examples

HTML sources are configurable in Django admin through the `config` JSON field. Prefer selectors over code changes when a site layout is stable enough.

Basic selector-based source:

```json
{
  "item_selector": "article.news-card",
  "title_selector": "h2 a",
  "link_selector": "h2 a",
  "date_selector": "time",
  "date_attr": "datetime",
  "summary_selector": ".summary",
  "author_selector": ".byline",
  "thumbnail_selector": "img",
  "thumbnail_attr": "src"
}
```

URL filtering for generic fallback mode:

```json
{
  "url_include_patterns": ["/news/", "/articles/", "/whatsnew/"],
  "url_exclude_patterns": ["/privacy", "/support", "/login"],
  "title_include_keywords": ["Nintendo", "Switch", "Direct"],
  "title_exclude_keywords": ["newsletter", "podcast"],
  "title_min_length": 10
}
```

HTTP tuning, kept intentionally conservative:

```json
{
  "timeout_seconds": 10,
  "retries": 1,
  "max_response_bytes": 5000000,
  "http_headers": {
    "Accept-Language": "ko,en;q=0.8,ja;q=0.7"
  }
}
```

Protected headers such as `Authorization` and `Cookie` are ignored. Nintendo Watch should not scrape login-only, paywalled, or protected content.

YouTube RSS source:

```json
{
  "channel_id": "YOUR_CHANNEL_ID"
}
```

## Current MVP Limits

- HTML sources use configurable CSS selectors when provided, otherwise a conservative generic link fallback.
- Korean summaries are rule-based and do not call external LLM APIs.
- Issue grouping uses normalized title token overlap within the last 14 days.
- YouTube Korea is seeded disabled until a channel ID is entered in Django admin.
- Search is simple database text matching, not PostgreSQL full-text search yet.
