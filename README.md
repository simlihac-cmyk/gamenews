# Nintendo Watch

Nintendo Watch는 개인용 닌텐도 뉴스/루머 아카이브입니다. 공식 뉴스, 해외 매체 보도, 루머/유출, 트레일러, Direct 관련 소식, 발매일 변경 등을 수집해서 Django 웹 UI에서 한국어로 빠르게 훑어볼 수 있게 만든 앱입니다.

배포 도메인:

```text
https://gamenews.monosaccharide180.com/
```

관리자:

```text
https://gamenews.monosaccharide180.com/admin/
```

웹 UI는 로그인해야 볼 수 있습니다.

## 빠른 시작

개발용 실행:

```bash
cp .env.example .env
docker compose up --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py seed_sources
docker compose exec web python manage.py fetch_news --limit 20
```

개발 서버는 Mac mini 호스트의 `7500` 포트에서 열립니다.

```text
http://127.0.0.1:7500/
```

배포용 실행:

```bash
cp .env.production.example .env
docker compose -f docker-compose.prod.yml up --build -d
docker compose -f docker-compose.prod.yml exec web python manage.py migrate
docker compose -f docker-compose.prod.yml exec web python manage.py collectstatic --noinput
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
docker compose -f docker-compose.prod.yml exec web python manage.py seed_sources
docker compose -f docker-compose.prod.yml exec web python manage.py fetch_news --limit 20
```

## .env 설정

배포할 때는 `.env.production.example`을 복사해서 `.env`를 만듭니다.

```bash
cp .env.production.example .env
```

### 반드시 바꿔야 하는 값

`SECRET_KEY`

```env
SECRET_KEY=replace-with-a-long-random-secret
```

반드시 긴 랜덤 문자열로 바꾸세요. 예:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

생성된 값을 `.env`의 `SECRET_KEY=` 뒤에 넣으면 됩니다.

### 배포 도메인 값

현재 배포 도메인은 아래 값으로 맞춰져 있습니다.

```env
ALLOWED_HOSTS=gamenews.monosaccharide180.com
CSRF_TRUSTED_ORIGINS=https://gamenews.monosaccharide180.com
```

도메인을 바꾸지 않는다면 그대로 두면 됩니다.

### HTTPS / Reverse Proxy 설정

운영 배포에서는 HTTPS를 쓰므로 아래 값은 그대로 두는 것을 권장합니다.

```env
SECURE_SSL_REDIRECT=true
SESSION_COOKIE_SECURE=true
CSRF_COOKIE_SECURE=true
USE_X_FORWARDED_PROTO=true
USE_X_FORWARDED_HOST=true
```

Caddy, nginx, Cloudflare Tunnel 같은 reverse proxy가 HTTPS를 받고 production Docker 서비스의 `7974` 포트로 넘기는 구조를 전제로 합니다. proxy는 최소한 아래 헤더를 넘겨야 합니다.

```text
Host: gamenews.monosaccharide180.com
X-Forwarded-Proto: https
```

현재 production compose는 Mac mini의 `127.0.0.1:7974`를 컨테이너 내부 `7974` 포트로 연결합니다. KBO 배포처럼 외부에서는 Cloudflare Tunnel만 앱에 접근하고, reverse proxy나 Cloudflare Tunnel은 `localhost:7974`로 넘기면 됩니다.

로컬 개발 중에는 `.env.example`을 쓰면 되고, 그 경우 HTTPS 관련 값은 필요 없습니다.

### 데이터베이스

Docker Compose 기준 기본값입니다.

```env
DATABASE_URL=postgres://nintendowatch:nintendowatch@db:5432/nintendowatch
```

배포 DB 비밀번호를 바꾸고 싶다면 `docker-compose.prod.yml`의 Postgres 환경변수와 `.env`의 `DATABASE_URL`을 같이 바꿔야 합니다.

### 알림 설정

알림은 기본적으로 꺼져 있습니다.

```env
NOTIFICATIONS_ENABLED=false
```

알림을 켜려면:

```env
NOTIFICATIONS_ENABLED=true
NOTIFICATION_MIN_IMPORTANCE=80
```

`NOTIFICATION_MIN_IMPORTANCE=80`은 중요도 80점 이상만 알림을 보낸다는 뜻입니다.

ntfy를 쓰려면:

```env
NTFY_SERVER=https://ntfy.sh
NTFY_TOPIC=your-topic
```

Discord webhook을 쓰려면:

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

둘 다 설정하면 중요 뉴스가 ntfy와 Discord 양쪽으로 전송됩니다. 성공한 알림은 항목/채널별로 중복 발송하지 않습니다.

### 수집 타임아웃

```env
COLLECTOR_TIMEOUT_SECONDS=10
```

각 소스 HTTP 요청의 기본 타임아웃입니다. 너무 크게 잡으면 깨진 소스 하나가 오래 기다리게 되므로 10초 정도를 권장합니다.

## 수동 명령

기본 소스와 프랜차이즈 생성:

```bash
docker compose exec web python manage.py seed_sources
```

전체 활성 소스 수집:

```bash
docker compose exec web python manage.py fetch_news --limit 20
```

특정 소스만 수집:

```bash
docker compose exec web python manage.py fetch_news --source gematsu --limit 20
```

알림 포함 수집:

```bash
docker compose exec web python manage.py fetch_news --limit 20 --notify
```

실제 저장 없이 확인:

```bash
docker compose exec web python manage.py fetch_news --limit 20 --dry-run
```

기존 항목 재계산:

```bash
docker compose exec web python manage.py recalculate_items
```

테스트:

```bash
docker compose exec web python manage.py test
```

## 24시간 운영

Mac mini에서 배포용 Compose를 계속 띄웁니다.

```bash
docker compose -f docker-compose.prod.yml up -d
```

주기적 수집은 `launchd` 예시를 사용할 수 있습니다.

```bash
cp deploy/launchd/com.nintendowatch.fetch.plist.example ~/Library/LaunchAgents/com.nintendowatch.fetch.plist
launchctl load ~/Library/LaunchAgents/com.nintendowatch.fetch.plist
```

자세한 배포/운영 흐름은 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)를 참고하세요.

## 개발 repo와 배포 repo 분리

현재 checkout은 개발용 repo로 두고, 배포용 GitHub repo는 `deploy` remote로 관리합니다.

```bash
git remote add deploy git@github.com:simlihac-cmyk/gamenews.git
git push deploy main
```

이후 배포용 repo로 릴리스할 때:

```bash
git status
git add .
git commit -m "변경 내용"
./scripts/deploy_push.sh deploy main
```

실험 중인 변경은 개발 repo에 두고, 배포 준비가 된 커밋만 배포 repo로 push하는 방식입니다.

## 소스 상태 확인

`/sources/health/`에서 소스별 상태를 볼 수 있습니다.

각 fetch는 `Source`에 다음 값을 기록합니다.

- `last_checked_at`: 마지막 수집 시도 시간
- `last_success_at`: 마지막 성공 시간
- `last_error`: 최근 오류
- `last_new_items_count`: 최근 수집에서 새로 저장된 원본 항목 수

깨진 소스 하나가 전체 fetch job을 멈추지 않도록 처리되어 있습니다.

## HTML Source.config 예시

HTML 소스는 Django admin의 `Source.config` JSON으로 selector를 설정할 수 있습니다.

기본 selector 방식:

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

generic fallback URL 필터:

```json
{
  "url_include_patterns": ["/news/", "/articles/", "/whatsnew/"],
  "url_exclude_patterns": ["/privacy", "/support", "/login"],
  "title_include_keywords": ["Nintendo", "Switch", "Direct"],
  "title_exclude_keywords": ["newsletter", "podcast"],
  "title_min_length": 10
}
```

HTTP 설정:

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

`Authorization`, `Cookie` 같은 보호 헤더는 무시합니다. 로그인 필요 페이지, 유료 페이지, 보호된 콘텐츠를 우회 수집하지 않습니다.

YouTube RSS:

```json
{
  "channel_id": "YOUR_CHANNEL_ID"
}
```

## 현재 한계

- HTML 소스는 selector 품질에 따라 수집 정확도가 달라집니다.
- 한국어 요약은 rule-based이며 외부 LLM API를 호출하지 않습니다.
- 이슈 그룹핑은 최근 14일 제목 토큰 겹침 기반입니다.
- YouTube Korea 소스는 channel ID를 입력한 뒤 활성화해야 합니다.
- 검색은 아직 PostgreSQL full-text search가 아니라 단순 DB 검색입니다.
