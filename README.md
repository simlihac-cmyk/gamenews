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

노트북에서 Mac mini의 개발 서버로 접속할 때는:

```text
http://SG-MACui-Macmini.local:7500/
```

### 개발 서버 한 줄 명령

개발 서버 켜기:

```bash
cd /Users/sg_mac/gamenews_dev && docker compose up -d --build
```

개발 서버 로그 보기:

```bash
cd /Users/sg_mac/gamenews_dev && docker compose logs -f web
```

개발 서버 끄기:

```bash
cd /Users/sg_mac/gamenews_dev && docker compose down
```

처음 한 번만 DB 초기화가 필요하면:

```bash
cd /Users/sg_mac/gamenews_dev && docker compose exec web python manage.py migrate && docker compose exec web python manage.py seed_sources
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

## 한 줄 릴리스

개발 repo(`/Users/sg_mac/gamenews_dev`)에서 배포 repo(`/Users/sg_mac/gamenews`)로 반영할 때는 버전을 붙여 아래 한 줄을 실행합니다.

```bash
./scripts/release_to_deploy.sh v1.0.0
```

커밋 메시지를 직접 쓰고 싶으면 버전 뒤에 붙이면 됩니다.

```bash
./scripts/release_to_deploy.sh v1.0.1 "알림과 이슈 추적 보강"
```

이 명령은 다음 작업을 순서대로 처리합니다.

- 개발 repo의 현재 변경사항을 `git add -A`로 스테이징
- 변경사항이 있으면 `Release vX.Y.Z` 커밋 생성
- `vX.Y.Z` annotated tag 생성
- `deploy` remote의 `main` 브랜치와 tag push
- `/Users/sg_mac/gamenews` 배포 폴더에서 `git pull --ff-only`
- production Docker 이미지 재빌드 및 컨테이너 재기동
- production DB migration과 `collectstatic` 실행

기본값은 아래처럼 바꿀 수 있습니다.

```bash
DEPLOY_APP_DIR=/Users/sg_mac/gamenews DEPLOY_REMOTE=deploy DEPLOY_BRANCH=main ./scripts/release_to_deploy.sh v1.0.0
```

개발 컨테이너가 떠 있으면 릴리스 전에 테스트를 실행합니다. 테스트를 건너뛰려면:

```bash
RELEASE_RUN_TESTS=0 ./scripts/release_to_deploy.sh v1.0.0
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

### Mac mini launchd

KBO 배포처럼 Mac mini 로그인/재부팅 후 운영 컨테이너를 다시 올리려면:

```bash
cp deploy/launchd/com.sg_mac.gamenews.plist.example ~/Library/LaunchAgents/com.sg_mac.gamenews.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.sg_mac.gamenews.plist
launchctl kickstart -k gui/$(id -u)/com.sg_mac.gamenews
```

이 launchd 작업은 `/Users/sg_mac/gamenews/deploy/start-prod.sh`를 실행해서 `docker compose -f docker-compose.prod.yml up -d`를 호출합니다.

뉴스 수집을 30분마다 실행하려면:

```bash
cp deploy/launchd/com.nintendowatch.fetch.plist.example ~/Library/LaunchAgents/com.nintendowatch.fetch.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nintendowatch.fetch.plist
launchctl kickstart -k gui/$(id -u)/com.nintendowatch.fetch
```

PostgreSQL 백업을 매일 04:10에 실행하려면:

```bash
cp deploy/launchd/com.nintendowatch.backup.plist.example ~/Library/LaunchAgents/com.nintendowatch.backup.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nintendowatch.backup.plist
launchctl kickstart -k gui/$(id -u)/com.nintendowatch.backup
```

백업 파일은 `/Users/sg_mac/gamenews/backups/postgres/`에 `nintendowatch-YYYYMMDD-HHMMSS.sql.gz` 형식으로 저장되고, 기본 보관 기간은 30일입니다.

이슈 stale 처리와 저중요 오래된 뉴스 archive를 매일 03:25에 실행하려면:

```bash
cp deploy/launchd/com.nintendowatch.maintenance.plist.example ~/Library/LaunchAgents/com.nintendowatch.maintenance.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nintendowatch.maintenance.plist
launchctl kickstart -k gui/$(id -u)/com.nintendowatch.maintenance
```

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

### 이슈/루머 추적

새 뉴스가 저장될 때 최근 14일 이내 이슈와 자동으로 연결합니다.

- 제목 정규화 토큰 겹침
- 같은/연관 카테고리
- 같은 게임종류 감지 결과
- 공식 출처가 기존 루머/전개 중 이슈를 확인하는지 여부

이슈 상태는 `루머 관찰 중`, `전개 중`, `공식 확정`, `반박됨`, `오래됨`으로 표시됩니다. 공식 뉴스가 기존 루머/전개 중 이슈와 연결되면 `공식 확정`으로 바뀌고 공식 확인 시각이 기록됩니다.

타임라인 카드에는 연결된 이슈 상태와 관련 뉴스 수가 표시됩니다. `/issues/`에서는 상태별 필터와 검색으로 루머 흐름을 빠르게 좁혀볼 수 있습니다.

반박 여부는 자동 추론을 공격적으로 하지 않습니다. Django admin의 Issue 화면에서 이슈를 수동으로 `공식 확정`, `반박됨`, `오래됨`으로 표시하거나, 중복 이슈를 선택해서 가장 오래된 이슈로 병합할 수 있습니다.

### 수집 타임아웃

```env
COLLECTOR_TIMEOUT_SECONDS=10
```

각 소스 HTTP 요청의 기본 타임아웃입니다. 너무 크게 잡으면 깨진 소스 하나가 오래 기다리게 되므로 10초 정도를 권장합니다.

### 한국어 요약

수집된 항목은 기본적으로 로컬 규칙 기반 한국어 요약을 생성합니다. 외부 API를 쓰지 않기 때문에 기본 설정에서는 비용이 들지 않습니다.

```env
SUMMARY_PROVIDER=rules
```

더 자연스러운 요약이 필요하면 OpenAI Responses API 기반 요약을 선택적으로 켤 수 있습니다.

```env
SUMMARY_PROVIDER=auto
OPENAI_API_KEY=sk-...
SUMMARY_OPENAI_MODEL=gpt-5-mini
SUMMARY_TIMEOUT_SECONDS=20
SUMMARY_MAX_SOURCE_CHARS=3000
```

`auto`는 OpenAI 요약이 실패하면 로컬 규칙 요약으로 자동 fallback합니다. `openai`도 실패 시 수집이 막히지 않도록 규칙 요약으로 fallback하되 경고 로그를 남깁니다.

ChatGPT Pro나 Gemini 웹앱 구독을 이미 쓰고 있고 API 비용을 추가하고 싶지 않다면, 반자동 배치 방식도 사용할 수 있습니다. 먼저 요약이 필요한 항목을 웹앱에 붙여넣을 프롬프트로 내보냅니다.

```bash
docker compose exec -T web python manage.py export_summary_batch --limit 20 --target chatgpt --show-skips > nintendowatch-summary-prompt.md
```

`nintendowatch-summary-prompt.md` 내용을 ChatGPT 또는 Gemini 웹앱에 붙여넣으면 모델이 JSON만 반환하도록 지시합니다. 그 응답을 `nintendowatch-summary-result.json` 같은 파일로 저장한 뒤 먼저 dry-run으로 검증합니다.

기본적으로 중요도 75점 이상 항목은 `summary_mode=detailed`로 내보냅니다. ChatGPT/Gemini 웹앱이 원문 URL을 열 수 있으면 `source_url`을 직접 확인해 `핵심 내용` bullet이 포함된 더 구체적인 요약을 만들도록 지시합니다. 75점 미만 항목은 기존처럼 간단한 4줄 요약으로 충분하게 처리합니다.

상세 요약 기준을 바꾸고 싶으면:

```bash
docker compose exec -T web python manage.py export_summary_batch --limit 20 --target chatgpt --detailed-threshold 80 --show-skips > nintendowatch-summary-prompt.md
```

```bash
docker compose exec -T web python manage.py import_summary_batch --input - --dry-run < nintendowatch-summary-result.json
```

검증 결과가 괜찮으면 실제 반영합니다.

```bash
docker compose exec -T web python manage.py import_summary_batch --input - < nintendowatch-summary-result.json
```

이미 있는 요약도 웹앱 결과로 덮어쓰고 싶으면 export와 import 양쪽에 `--force`를 붙입니다. import는 item id와 token을 확인하므로, 다른 DB나 오래된 export 파일의 응답이 섞이면 건너뜁니다.

export는 기본적으로 허브/목록 URL, 원문 발췌가 너무 짧은 항목, 추출 신뢰도가 낮은 항목을 건너뜁니다. 운영에서 `hub_url`, `raw_text_too_short`가 많이 보이면 먼저 아래 품질 검사를 확인하세요.

```bash
docker compose exec web python manage.py audit_news_quality --limit 100
```

`audit_news_quality`는 기본 dry-run입니다. `hub_url`, `date_suspect`, `empty_title`, `boilerplate_title`처럼 치명적인 항목만 기본 격리 대상으로 계산하고, `read_more_in_title`, `long_title`처럼 제목 정제만 필요한 항목은 보고만 합니다.

치명적인 항목만 실제 격리하려면:

```bash
docker compose exec web python manage.py audit_news_quality --limit 100 --apply
```

특정 이유만 보고 싶으면:

```bash
docker compose exec web python manage.py audit_news_quality --limit 100 --reasons hub_url,date_suspect
```

`read_more_in_title`, `long_title`까지 강제로 격리하려면 `--apply --apply-soft`가 필요합니다. 실제 기사까지 숨길 수 있으므로 보통은 쓰지 마세요. export에 정말 강제로 포함해야 할 때만 `--include-low-quality`를 사용하세요.

### 백업 상태 경로

상태 화면에서 최근 백업 파일을 확인할 때 쓰는 경로입니다.

```env
BACKUP_DIR=/Users/sg_mac/gamenews/backups/postgres
```

## 수동 명령

기본 소스와 게임종류 생성:

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

기존 항목의 한국어 요약만 다시 생성:

```bash
docker compose exec web python manage.py summarize_items --limit 100
```

이미 있는 요약까지 강제로 다시 만들려면:

```bash
docker compose exec web python manage.py summarize_items --force --provider auto --limit 100
```

ChatGPT/Gemini 웹앱용 반자동 요약 배치:

```bash
docker compose exec -T web python manage.py export_summary_batch --limit 20 --target gemini --show-skips > nintendowatch-summary-prompt.md
docker compose exec -T web python manage.py import_summary_batch --input - --dry-run < nintendowatch-summary-result.json
docker compose exec -T web python manage.py import_summary_batch --input - < nintendowatch-summary-result.json
```

저중요 오래된 뉴스를 보관 처리:

```bash
docker compose exec web python manage.py archive_low_value_items --days 60 --max-importance 25
```

읽지 않은 항목까지 포함하려면:

```bash
docker compose exec web python manage.py archive_low_value_items --days 60 --max-importance 25 --include-unread
```

오래된 루머/전개 중 이슈를 `오래됨`으로 표시:

```bash
docker compose exec web python manage.py mark_stale_issues --days 30
```

실제 변경 없이 확인:

```bash
docker compose exec web python manage.py mark_stale_issues --days 30 --dry-run
```

알림 설정 테스트:

```bash
docker compose exec web python manage.py test_notifications --channel discord
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

운영 유지보수 작업을 한 번 직접 실행:

```bash
./scripts/macmini_maintenance.sh
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

`fetch_news --notify`로 실행하면 source fetch 오류도 Discord 운영 알림으로 전송합니다. 같은 source에서 같은 오류가 반복될 때는 중복 알림을 보내지 않습니다.

상태 화면에는 최근 PostgreSQL 백업 파일도 함께 표시됩니다.

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

`title_include_keywords`, `title_exclude_keywords`, `url_include_patterns`, `url_exclude_patterns`는 RSS/HTML 저장 직전 공통으로 적용됩니다. Gematsu, Nintendo Life, VGC, Reddit 기본 소스는 닌텐도 관련 키워드 중심으로 노이즈를 줄이도록 seed되어 있습니다.

Next.js/embedded JSON 방식:

```json
{
  "embedded_json_selector": "script#__NEXT_DATA__",
  "embedded_json_item_type": "NewsArticle",
  "embedded_json_title_fields": ["title"],
  "embedded_json_url_fields": ["url({\"relative\":true})"],
  "embedded_json_summary_fields": ["body.text({\"characterLimit\":250})"],
  "embedded_json_date_fields": ["publishDate"],
  "url_include_patterns": ["/us/whatsnew/"]
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
- 한국어 요약은 기본값이 rule-based입니다. `SUMMARY_PROVIDER=auto` 또는 `openai`를 설정하면 선택적으로 LLM 요약을 사용할 수 있습니다.
- 이슈 그룹핑은 최근 14일 제목 토큰, 카테고리, 게임종류 기반의 단순 규칙입니다.
- YouTube Korea 소스는 공식 channel ID를 기록해 두었지만, 현재 YouTube RSS endpoint가 404를 반환하므로 기본 비활성입니다.
- 검색은 아직 PostgreSQL full-text search가 아니라 단순 DB 검색입니다.
