from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Case, Count, F, IntegerField, Max, Prefetch, Q, Sum, Value, When
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import NewsItemFilterForm
from .models import Franchise, Issue, IssueStatus, NewsItem, NewsItemIssue, Source, UserFranchiseFavorite
from .services.collectors import collect_source, process_raw_item, recalculate_news_item
from .services.quality import LOW_CONFIDENCE_THRESHOLD, is_generic_summary


PAGE_SIZE = 25

PUBLIC_TIMELINE_DESCRIPTION = (
    "닌텐도 공식 뉴스, 보도, 루머, 발매일, Direct 소식을 출처와 이슈 흐름별로 모아보는 비공식 뉴스 모니터링 사이트입니다."
)


def _absolute_url(request, view_name: str, *args, query: str = "") -> str:
    path = reverse(view_name, args=args)
    if query:
        path = f"{path}?{query}"
    return request.build_absolute_uri(path)


def _public_items_queryset():
    return NewsItem.objects.filter(
        is_archived=False,
        extraction_confidence__gte=LOW_CONFIDENCE_THRESHOLD,
        raw_item__rejection_reason="",
    )


def _order_items(queryset, sort: str = "published"):
    if sort == "detected":
        return queryset.order_by("-first_seen_at", F("published_at").desc(nulls_last=True), "-created_at")
    return queryset.order_by(F("published_at").desc(nulls_last=True), "-first_seen_at", "-created_at")


def _seo_context(
    request,
    *,
    canonical_url: str,
    description: str = PUBLIC_TIMELINE_DESCRIPTION,
    robots: str = "index,follow",
) -> dict[str, str]:
    return {
        "seo_canonical_url": canonical_url,
        "seo_description": _compact_description(description),
        "seo_robots": robots,
    }


def _compact_description(value: str, fallback: str = PUBLIC_TIMELINE_DESCRIPTION) -> str:
    compact = " ".join((value or fallback).split())
    if len(compact) > 160:
        compact = compact[:157].rstrip() + "..."
    return compact


def _item_description(item: NewsItem) -> str:
    published = item.published_at.strftime("%Y-%m-%d") if item.published_at else "미상"
    if item.summary_ko and not is_generic_summary(item.summary_ko):
        return item.summary_ko
    return f"{item.source.name}에서 게시된 {item.title} 관련 소식입니다. 게시일 {published}, 수집일 {item.first_seen_at:%Y-%m-%d}."


def _sanitize_public_error(error: str) -> str:
    if not error:
        return ""
    lowered = error.lower()
    if any(marker in lowered for marker in ("traceback", "secret", "password", "database_url", "token=")):
        return "상세 오류는 관리자에게만 표시됩니다."
    value = error.replace(str(settings.BASE_DIR), "[path]")
    for marker in ("/app/", "/Users/", "backups/postgres"):
        if marker in value:
            return "상세 오류는 관리자에게만 표시됩니다."
    return value[:180]


def home(request):
    return redirect("news:item_list")


def item_list(request):
    form = NewsItemFilterForm(request.GET or None)
    items = (
        _public_items_queryset()
        .select_related("source", "raw_item")
        .prefetch_related(
            "franchise_links__franchise",
            Prefetch(
                "issue_links",
                queryset=NewsItemIssue.objects.select_related("issue").annotate(
                    issue_item_count=Count("issue__news_links", distinct=True)
                ),
            ),
        )
    )
    any_news_exists = _public_items_queryset().exists()
    sort = request.GET.get("sort") or "published"
    empty_state = ""
    favorites_active = False

    if form.is_valid():
        data = form.cleaned_data
        sort = data.get("sort") or "published"
        if data.get("q"):
            query = data["q"]
            search_filter = (
                Q(title__icontains=query)
                | Q(summary_ko__icontains=query)
                | Q(summary_original__icontains=query)
                | Q(source__name__icontains=query)
                | Q(raw_item__raw_text__icontains=query)
            )
            if connection.vendor == "postgresql":
                vector = (
                    SearchVector("title", weight="A", config="simple")
                    + SearchVector("summary_ko", weight="B", config="simple")
                    + SearchVector("summary_original", weight="C", config="simple")
                    + SearchVector("source__name", weight="D", config="simple")
                )
                search_query = SearchQuery(query, config="simple", search_type="websearch")
                items = items.annotate(search_vector=vector, rank=SearchRank(vector, search_query)).filter(
                    Q(search_vector=search_query) | search_filter
                ).order_by("-rank", "-published_at", "-first_seen_at", "-created_at")
            else:
                items = items.filter(search_filter)
        if data.get("trust_label"):
            items = items.filter(trust_label=data["trust_label"])
        if data.get("category"):
            items = items.filter(category=data["category"])
        if data.get("source"):
            items = items.filter(source=data["source"])
        if data.get("franchise"):
            items = items.filter(franchise_links__franchise=data["franchise"])
        if data.get("is_read") in {"true", "false"}:
            items = items.filter(is_read=data["is_read"] == "true")
        if data.get("is_bookmarked") in {"true", "false"}:
            items = items.filter(is_bookmarked=data["is_bookmarked"] == "true")
        if data.get("favorites_only"):
            favorites_active = True
            if not request.user.is_authenticated:
                items = items.none()
                empty_state = "login_required_favorites"
            else:
                favorite_ids = list(
                    UserFranchiseFavorite.objects.filter(user=request.user).values_list("franchise_id", flat=True)
                )
                if favorite_ids:
                    items = items.filter(franchise_links__franchise_id__in=favorite_ids)
                else:
                    items = items.none()
                    empty_state = "no_favorites"
        if data.get("min_importance") is not None:
            items = items.filter(importance_score__gte=data["min_importance"])
        date_field = "first_seen_at__date" if sort == "detected" else "published_at__date"
        if data.get("date_from"):
            items = items.filter(**{f"{date_field}__gte": data["date_from"]})
        if data.get("date_to"):
            items = items.filter(**{f"{date_field}__lte": data["date_to"]})

    items = _order_items(items.distinct(), sort)
    paginator = Paginator(items, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    today = timezone.localdate()
    has_filter_params = any(key != "page" for key in request.GET)
    quick_filters = [
        {"label": "전체", "query": ""},
        {"label": "오늘", "query": urlencode({"date_from": today.isoformat()})},
        {"label": "7일", "query": urlencode({"date_from": (today - timedelta(days=7)).isoformat()})},
        {"label": "공식", "query": urlencode({"trust_label": "official"})},
        {"label": "루머", "query": urlencode({"trust_label": "rumor"})},
        {"label": "Switch 2", "query": urlencode({"q": "Switch 2"})},
        {"label": "중요 80+", "query": urlencode({"min_importance": 80})},
    ]
    current_query = query_params.urlencode()
    for quick_filter in quick_filters:
        quick_filter["active"] = quick_filter["query"] == current_query
    if not current_query:
        quick_filters[0]["active"] = True

    if paginator.count == 0 and not empty_state:
        if not any_news_exists:
            empty_state = "no_items"
        elif form.is_valid() and form.cleaned_data.get("q"):
            empty_state = "no_search_results"
        elif has_filter_params:
            empty_state = "no_filter_results"
        else:
            empty_state = "no_items"

    seo = _seo_context(
        request,
        canonical_url=_absolute_url(request, "news:item_list"),
        robots="noindex,follow" if has_filter_params else "index,follow",
    )
    return render(
        request,
        "news/item_list.html",
        {
            "form": form,
            "page": page,
            "query_params": query_params.urlencode(),
            "total_count": paginator.count,
            "quick_filters": quick_filters,
            "empty_state": empty_state,
            "favorites_active": favorites_active,
            "has_filter_params": has_filter_params,
            **seo,
        },
    )


def item_detail(request, pk: int):
    item = get_object_or_404(
        _public_items_queryset().select_related("source", "raw_item").prefetch_related(
            "franchise_links__franchise", "issue_links__issue"
        ),
        pk=pk,
    )
    issue = item.issue_links.select_related("issue").first()
    related_items = NewsItem.objects.none()
    if issue:
        related_items = (
            _public_items_queryset()
            .filter(issue_links__issue=issue.issue)
            .exclude(pk=item.pk)
            .select_related("source")
            .order_by(F("published_at").desc(nulls_last=True), "-first_seen_at")[:20]
        )
    canonical_url = _absolute_url(request, "news:item_detail", item.pk)
    article_json_ld = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": item.title,
        "dateModified": item.updated_at.isoformat(),
        "isAccessibleForFree": True,
        "author": {"@type": "Organization", "name": item.source.name},
        "publisher": {"@type": "Organization", "name": "Nintendo Watch"},
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical_url},
    }
    if item.published_at:
        article_json_ld["datePublished"] = item.published_at.isoformat()
    return render(
        request,
        "news/item_detail.html",
        {
            "item": item,
            "issue_link": issue,
            "related_items": related_items,
            "show_summary": bool(item.summary_ko and not is_generic_summary(item.summary_ko)),
            "article_json_ld": json.dumps(article_json_ld, ensure_ascii=False),
            **_seo_context(
                request,
                canonical_url=canonical_url,
                description=_item_description(item),
            ),
        },
    )


def issue_list(request):
    issues = Issue.objects.annotate(
        item_count=Count(
            "news_links",
            filter=Q(news_links__news_item__is_archived=False, news_links__news_item__extraction_confidence__gte=LOW_CONFIDENCE_THRESHOLD),
            distinct=True,
        )
    )
    status = request.GET.get("status", "").strip()
    query = request.GET.get("q", "").strip()

    if status in IssueStatus.values:
        issues = issues.filter(status=status)
    if query:
        issues = issues.filter(
            Q(title__icontains=query)
            | Q(canonical_topic__icontains=query)
            | Q(news_links__news_item__title__icontains=query)
        )

    issues = issues.order_by("-last_updated_at").distinct()
    paginator = Paginator(issues, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "news/issue_list.html",
        {
            "page": page,
            "total_count": paginator.count,
            "query": query,
            "selected_status": status,
            "status_filters": [
                ("", "전체"),
                (IssueStatus.RUMOR, "루머 관찰 중"),
                (IssueStatus.DEVELOPING, "전개 중"),
                (IssueStatus.CONFIRMED, "공식 확정"),
                (IssueStatus.DEBUNKED, "반박됨"),
                (IssueStatus.STALE, "오래됨"),
            ],
            "query_params": query_params.urlencode(),
            **_seo_context(
                request,
                canonical_url=_absolute_url(request, "news:issue_list"),
                description="닌텐도 뉴스와 루머가 어떻게 전개되고 공식 확인되는지 이슈별로 묶어 보는 페이지입니다.",
                robots="noindex,follow" if request.GET else "index,follow",
            ),
        },
    )


def issue_detail(request, pk: int):
    issue = get_object_or_404(Issue, pk=pk)
    links = list(
        issue.news_links.filter(news_item__is_archived=False, news_item__extraction_confidence__gte=LOW_CONFIDENCE_THRESHOLD)
        .select_related("news_item", "news_item__source")
        .prefetch_related("news_item__franchise_links__franchise")
        .order_by(F("news_item__published_at").asc(nulls_last=True), "news_item__first_seen_at", "news_item__pk")
    )
    official_count = sum(1 for link in links if link.news_item.trust_label == "official")
    rumor_count = sum(1 for link in links if link.news_item.trust_label == "rumor")
    return render(
        request,
        "news/issue_detail.html",
        {
            "issue": issue,
            "links": links,
            "item_count": len(links),
            "official_count": official_count,
            "rumor_count": rumor_count,
            **_seo_context(
                request,
                canonical_url=_absolute_url(request, "news:issue_detail", issue.pk),
                description=f"{issue.title} 이슈의 관련 닌텐도 뉴스 {len(links)}건과 현재 상태를 확인합니다.",
            ),
        },
    )


def source_list(request):
    sources = Source.objects.annotate(
        item_count=Count("news_items", distinct=True),
        raw_count=Count("raw_items", distinct=True),
    ).order_by("name")
    for source in sources:
        source.public_last_error = _sanitize_public_error(source.last_error)
    return render(
        request,
        "news/source_list.html",
        {
            "sources": sources,
            **_seo_context(
                request,
                canonical_url=_absolute_url(request, "news:source_list"),
                description="Nintendo Watch가 확인하는 닌텐도 뉴스 출처와 최근 수집 상태입니다.",
            ),
        },
    )


def source_health(request):
    stale_cutoff = timezone.now() - timedelta(hours=24)
    sources = Source.objects.annotate(
        item_count=Count("news_items", distinct=True),
        raw_count=Count("raw_items", distinct=True),
        health_order=Case(
            When(enabled=False, then=Value(3)),
            When(last_error="", last_success_at__isnull=False, then=Value(2)),
            When(last_error="", then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        ),
    ).order_by("health_order", "name")
    totals = {
        "enabled": Source.objects.filter(enabled=True).count(),
        "disabled": Source.objects.filter(enabled=False).count(),
        "errors": Source.objects.filter(enabled=True).exclude(last_error="").count(),
        "stale": Source.objects.filter(enabled=True).filter(Q(last_success_at__isnull=True) | Q(last_success_at__lt=stale_cutoff)).count(),
        "items": NewsItem.objects.count(),
        "last_new": Source.objects.aggregate(total=Sum("last_new_items_count"))["total"] or 0,
        "status": "오류" if Source.objects.filter(enabled=True).exclude(last_error="").exists() else "주의" if Source.objects.filter(enabled=True).filter(Q(last_success_at__isnull=True) | Q(last_success_at__lt=stale_cutoff)).exists() else "정상",
        "recent_errors": Source.objects.filter(enabled=True).exclude(last_error="").count(),
        "last_updated_at": Source.objects.aggregate(last=Max("last_checked_at"))["last"],
    }
    for source in sources:
        source.public_last_error = _sanitize_public_error(source.last_error)
    return render(
        request,
        "news/source_health.html",
        {
            "sources": sources,
            "totals": totals,
            "backup_status": _backup_status(),
            **_seo_context(
                request,
                canonical_url=_absolute_url(request, "news:source_health"),
                description="Nintendo Watch 수집 상태, 최근 업데이트, 활성 출처, 오류와 백업 상태 요약입니다.",
            ),
        },
    )


@staff_member_required
def source_health_internal(request):
    return render(
        request,
        "news/source_health_internal.html",
        {
            "backup_status": _backup_status(include_internal=True),
            "sources": Source.objects.order_by("name"),
            **_seo_context(
                request,
                canonical_url=_absolute_url(request, "news:source_health_internal"),
                robots="noindex,nofollow",
            ),
        },
    )


def _backup_status(*, include_internal: bool = False) -> dict[str, object]:
    backup_dir = Path(settings.BACKUP_DIR)
    files = sorted(backup_dir.glob("nintendowatch-*.sql.gz"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        status = {"exists": False, "status_label": "미확인"}
        if include_internal:
            status["directory"] = str(backup_dir)
        return status
    latest = files[0]
    modified = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.get_current_timezone())
    status = {
        "exists": True,
        "status_label": "정상",
        "modified_at": modified,
        "size_mb": round(latest.stat().st_size / 1024 / 1024, 2),
    }
    if include_internal:
        status.update({"directory": str(backup_dir), "filename": latest.name, "path": str(latest)})
    return status


def franchise_list(request):
    franchises = Franchise.objects.annotate(item_count=Count("news_links")).order_by("-priority", "name")
    return render(
        request,
        "news/franchise_list.html",
        {
            "franchises": franchises,
            **_seo_context(
                request,
                canonical_url=_absolute_url(request, "news:franchise_list"),
                description="Nintendo Watch에서 별칭으로 추적하는 닌텐도 프랜차이즈 목록입니다.",
            ),
        },
    )


def franchise_favorites(request):
    if not request.user.is_authenticated:
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")
    franchises = list(Franchise.objects.annotate(item_count=Count("news_links")).order_by("-priority", "name"))
    if request.method == "POST":
        selected_ids = {int(value) for value in request.POST.getlist("franchises") if value.isdigit()}
        UserFranchiseFavorite.objects.filter(user=request.user).exclude(franchise_id__in=selected_ids).delete()
        existing_ids = set(UserFranchiseFavorite.objects.filter(user=request.user).values_list("franchise_id", flat=True))
        UserFranchiseFavorite.objects.bulk_create(
            [
                UserFranchiseFavorite(user=request.user, franchise_id=franchise_id)
                for franchise_id in selected_ids
                if franchise_id not in existing_ids
            ],
            ignore_conflicts=True,
        )
        messages.success(request, "관심 프랜차이즈를 저장했습니다.")
        return redirect("news:franchise_favorites")

    favorite_ids = set(UserFranchiseFavorite.objects.filter(user=request.user).values_list("franchise_id", flat=True))
    return render(
        request,
        "news/franchise_favorites.html",
        {
            "franchises": franchises,
            "favorite_ids": favorite_ids,
            **_seo_context(
                request,
                canonical_url=_absolute_url(request, "news:franchise_favorites"),
                robots="noindex,nofollow",
            ),
        },
    )


def franchise_detail(request, slug: str):
    franchise = get_object_or_404(Franchise, slug=slug)
    items = (
        _public_items_queryset()
        .filter(franchise_links__franchise=franchise)
        .select_related("source")
        .prefetch_related("franchise_links__franchise")
    )
    items = _order_items(items)
    paginator = Paginator(items, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "news/franchise_detail.html",
        {
            "franchise": franchise,
            "page": page,
            **_seo_context(
                request,
                canonical_url=_absolute_url(request, "news:franchise_detail", franchise.slug),
                description=f"{franchise.name} 관련 닌텐도 뉴스와 이슈를 모아보는 페이지입니다.",
            ),
        },
    )


@require_POST
def mark_read(request, pk: int):
    item = get_object_or_404(NewsItem, pk=pk)
    item.is_read = True
    item.save(update_fields=["is_read", "updated_at"])
    messages.success(request, "읽음으로 표시했습니다.")
    return _redirect_back(request, item)


@require_POST
def toggle_bookmark(request, pk: int):
    item = get_object_or_404(NewsItem, pk=pk)
    item.is_bookmarked = not item.is_bookmarked
    item.save(update_fields=["is_bookmarked", "updated_at"])
    messages.success(request, "북마크를 변경했습니다.")
    return _redirect_back(request, item)


@require_POST
def archive_item(request, pk: int):
    item = get_object_or_404(NewsItem, pk=pk)
    item.is_archived = True
    item.save(update_fields=["is_archived", "updated_at"])
    messages.success(request, "보관 처리했습니다.")
    return _redirect_back(request, item)


@require_POST
def recalculate_item(request, pk: int):
    item = get_object_or_404(NewsItem.objects.select_related("raw_item"), pk=pk)
    recalculate_news_item(item)
    messages.success(request, "분류와 중요도를 다시 계산했습니다.")
    return _redirect_back(request, item)


@require_POST
def refresh_source(request, pk: int):
    source = get_object_or_404(Source, pk=pk)
    result = collect_source(source, limit=20)
    created = 0
    rejected = 0
    for raw_item in result.raw_items:
        news_item, was_created = process_raw_item(raw_item)
        if news_item is None:
            rejected += 1
            continue
        created += int(was_created)
    messages.success(
        request,
        f"{source.name}: 원본 {result.created_count}개, 뉴스 {created}개를 새로 만들었습니다. 제외 {rejected}개.",
    )
    return redirect("news:source_list")


def _redirect_back(request, item: NewsItem):
    fallback = reverse("news:item_detail", args=[item.pk])
    return redirect(request.META.get("HTTP_REFERER") or fallback)


def sitemap_xml(request):
    urls: list[dict[str, object]] = [
        {"loc": _absolute_url(request, "news:item_list"), "lastmod": None},
        {"loc": _absolute_url(request, "news:issue_list"), "lastmod": None},
        {"loc": _absolute_url(request, "news:franchise_list"), "lastmod": None},
        {"loc": _absolute_url(request, "news:source_list"), "lastmod": None},
    ]
    for item in _public_items_queryset().filter(published_at__isnull=False).select_related("source").order_by("-updated_at")[:1000]:
        urls.append({"loc": _absolute_url(request, "news:item_detail", item.pk), "lastmod": item.updated_at})
    for issue in Issue.objects.filter(news_links__news_item__is_archived=False).distinct().order_by("-last_updated_at")[:500]:
        urls.append({"loc": _absolute_url(request, "news:issue_detail", issue.pk), "lastmod": issue.last_updated_at})
    for franchise in Franchise.objects.order_by("slug"):
        urls.append({"loc": _absolute_url(request, "news:franchise_detail", franchise.slug), "lastmod": None})

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for entry in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{entry['loc']}</loc>")
        if entry["lastmod"]:
            lines.append(f"    <lastmod>{entry['lastmod'].date().isoformat()}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return HttpResponse("\n".join(lines), content_type="application/xml")


def robots_txt(request):
    body = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "Disallow: /admin/",
            "Disallow: /accounts/",
            f"Sitemap: {request.build_absolute_uri(reverse('news:sitemap_xml'))}",
            "",
        ]
    )
    return HttpResponse(body, content_type="text/plain")
