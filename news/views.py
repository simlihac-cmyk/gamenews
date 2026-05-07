from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Case, Count, F, IntegerField, Max, Prefetch, Q, Sum, Value, When
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import NewsItemFilterForm
from .models import Franchise, Issue, IssueRelation, IssueStatus, NewsContentType, NewsItem, NewsItemIssue, RawItem, Source, UserFranchiseFavorite
from .services.collectors import collect_source, process_raw_item, recalculate_news_item
from .services.importance import reason_labels
from .services.quality import LOW_CONFIDENCE_THRESHOLD, fallback_summary_for, is_generic_summary, public_excerpt


PAGE_SIZE = 25
STATIC_CONTENT_TYPES = {
    NewsContentType.STATIC_PAGE,
    NewsContentType.LIST_PAGE,
    NewsContentType.HUB_PAGE,
}

PUBLIC_TIMELINE_DESCRIPTION = (
    "닌텐도 공식 뉴스, 보도, 루머, 발매일, Direct 소식을 출처와 이슈 흐름별로 모아보는 비공식 뉴스 모니터링 사이트입니다."
)


def _absolute_url(request, view_name: str, *args, query: str = "") -> str:
    path = reverse(view_name, args=args)
    if query:
        path = f"{path}?{query}"
    return request.build_absolute_uri(path)


def _public_items_queryset():
    now = timezone.now()
    return NewsItem.objects.filter(
        is_archived=False,
        extraction_confidence__gte=LOW_CONFIDENCE_THRESHOLD,
        raw_item__rejection_reason="",
        is_date_suspect=False,
    ).filter(
        Q(published_at__isnull=True) | Q(published_at__lte=now),
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


def _day_start(value):
    return timezone.make_aware(datetime.combine(value, time.min), timezone=timezone.get_current_timezone())


def _apply_datetime_range(queryset, field: str, *, date_from=None, date_to=None):
    if date_from:
        queryset = queryset.filter(**{f"{field}__gte": _day_start(date_from)})
    if date_to:
        queryset = queryset.filter(**{f"{field}__lt": _day_start(date_to + timedelta(days=1))})
    elif date_from:
        queryset = queryset.filter(**{f"{field}__lte": timezone.now()})
    return queryset


def _home_headline_sections() -> list[dict[str, object]]:
    base = (
        _public_items_queryset()
        .select_related("source")
        .prefetch_related("franchise_links__franchise", "issue_links__issue")
        .filter(
            published_at__isnull=False,
            title_suspect=False,
            nintendo_relevance_score__gte=3,
        )
        .exclude(date_confidence="low")
        .exclude(content_type__in=STATIC_CONTENT_TYPES)
        .exclude(is_backfill=True)
        .exclude(importance_reasons=[])
        .exclude(issue_links__issue__review_required=True)
        .distinct()
    )
    today = timezone.localdate()
    today_start = _day_start(today)
    now = timezone.now()

    headline_items = _headline_candidates_with_fallback(
        base.filter(trust_label__in=["official", "reported"]),
        tiers=[
            Q(published_at__gte=today_start, importance_score__gte=80),
            Q(first_seen_at__gte=now - timedelta(hours=24), importance_score__gte=80),
            Q(first_seen_at__gte=now - timedelta(hours=48), importance_score__gte=70),
        ],
        order_by=["-importance_score", "-confidence_score", F("published_at").desc(nulls_last=True)],
    )
    official_items = _headline_candidates_with_fallback(
        base.filter(trust_label="official"),
        tiers=[
            Q(published_at__gte=today_start),
            Q(first_seen_at__gte=now - timedelta(hours=24)),
            Q(first_seen_at__gte=now - timedelta(hours=48)),
        ],
        order_by=["-confidence_score", "-importance_score", F("published_at").desc(nulls_last=True)],
    )
    rumor_items = _headline_candidates_with_fallback(
        base.filter(Q(trust_label="rumor") | Q(category__in=["rumor", "leak"])),
        tiers=[
            Q(published_at__gte=today_start),
            Q(first_seen_at__gte=now - timedelta(hours=24)),
            Q(first_seen_at__gte=now - timedelta(hours=48)),
        ],
        order_by=["-importance_score", F("published_at").desc(nulls_last=True)],
    )
    return [
        {
            "title": "오늘의 핵심 5개",
            "items": headline_items,
            "empty": "아직 오늘 핵심으로 뽑을 항목이 없습니다.",
        },
        {
            "title": "공식 확인된 소식",
            "items": official_items,
            "empty": "최근 공식 확인 소식이 없습니다.",
        },
        {
            "title": "관찰 중인 루머",
            "items": rumor_items,
            "empty": "현재 표시할 루머 항목이 없습니다.",
            "rumor": True,
        },
    ]


def _headline_candidates_with_fallback(queryset, *, tiers: list[Q], order_by: list[object]) -> list[NewsItem]:
    for tier in tiers:
        candidates = list(queryset.filter(tier).order_by(*order_by).distinct()[:20])
        items = [item for item in candidates if reason_labels(item.importance_reasons)][:5]
        if items:
            return items
    return []


def _show_internal_debug(request) -> bool:
    return bool(settings.DEBUG or getattr(request.user, "is_staff", False))


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


def _source_status(source: Source) -> dict[str, str]:
    if not source.enabled:
        return {"label": "비활성", "class": "state", "reason": (source.config or {}).get("inactive_reason", "관리자 비활성화")}
    if source.last_error:
        return {"label": "오류", "class": "debunked", "reason": _sanitize_public_error(source.last_error) or "최근 요청 실패"}
    if not source.last_success_at:
        return {"label": "지연", "class": "state", "reason": "아직 성공 기록 없음"}
    elapsed = timezone.now() - source.last_success_at
    stale_after = timedelta(minutes=max(source.poll_interval_minutes * 2, 360))
    if elapsed > stale_after:
        return {"label": "지연", "class": "state", "reason": f"{int(elapsed.total_seconds() // 3600)}시간 이상 지연"}
    return {"label": "정상", "class": "confirmed", "reason": "최근 수집 성공"}


def _group_sources(sources) -> list[dict[str, object]]:
    order = ["official", "press", "rumor", "other"]
    grouped = {key: {"key": key, "label": "", "description": "", "sources": []} for key in order}
    for source in sources:
        group = grouped.setdefault(
            source.source_group_key,
            {"key": source.source_group_key, "label": source.source_group_ko, "description": source.source_group_description, "sources": []},
        )
        group["label"] = source.source_group_ko
        group["description"] = source.source_group_description
        group["sources"].append(source)
    return [grouped[key] for key in order if grouped.get(key, {}).get("sources")]


def _active_filter_chips(request, form: NewsItemFilterForm) -> list[dict[str, str]]:
    if not form.is_valid():
        return []
    data = form.cleaned_data
    chips: list[dict[str, str]] = []

    def remove_query(*keys: str) -> str:
        query = request.GET.copy()
        for key in keys:
            query.pop(key, None)
        query.pop("page", None)
        encoded = query.urlencode()
        return f"{reverse('news:item_list')}?{encoded}" if encoded else reverse("news:item_list")

    if data.get("q"):
        chips.append({"label": f"검색: {data['q']}", "url": remove_query("q")})
    if data.get("trust_label"):
        chips.append({"label": f"신뢰도: {dict(form.fields['trust_label'].choices).get(data['trust_label'], data['trust_label'])}", "url": remove_query("trust_label")})
    if data.get("category"):
        chips.append({"label": f"카테고리: {dict(form.fields['category'].choices).get(data['category'], data['category'])}", "url": remove_query("category")})
    if data.get("source"):
        chips.append({"label": f"출처: {data['source'].name}", "url": remove_query("source")})
    if data.get("franchise"):
        chips.append({"label": f"게임종류: {data['franchise'].name}", "url": remove_query("franchise")})
    if data.get("min_importance") is not None:
        chips.append({"label": f"중요도 {data['min_importance']}+", "url": remove_query("min_importance")})
    if data.get("date_from") or data.get("date_to"):
        start = data.get("date_from").isoformat() if data.get("date_from") else "처음"
        end = data.get("date_to").isoformat() if data.get("date_to") else "현재"
        chips.append({"label": f"날짜: {start} ~ {end}", "url": remove_query("date_from", "date_to")})
    if data.get("is_read") in {"true", "false"}:
        chips.append({"label": "읽음" if data["is_read"] == "true" else "읽지 않음", "url": remove_query("is_read")})
    if data.get("is_bookmarked") in {"true", "false"}:
        chips.append({"label": "북마크" if data["is_bookmarked"] == "true" else "북마크 아님", "url": remove_query("is_bookmarked")})
    if data.get("favorites_only"):
        chips.append({"label": "관심작만", "url": remove_query("favorites_only")})
    if data.get("sort") == "detected":
        chips.append({"label": "수집순", "url": remove_query("sort")})
    return chips


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
            items = items.filter(franchise_links__franchise=data["franchise"], franchise_links__is_primary=True)
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
                    items = items.filter(franchise_links__franchise_id__in=favorite_ids, franchise_links__is_primary=True)
                else:
                    items = items.none()
                empty_state = "no_favorites"
        if data.get("min_importance") is not None:
            items = items.filter(importance_score__gte=data["min_importance"])
        date_field = "first_seen_at" if sort == "detected" else "published_at"
        items = _apply_datetime_range(
            items,
            date_field,
            date_from=data.get("date_from"),
            date_to=data.get("date_to"),
        )

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

    headline_sections = _home_headline_sections() if not has_filter_params else []
    active_filter_chips = _active_filter_chips(request, form)
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
            "headline_sections": headline_sections,
            "active_filter_chips": active_filter_chips,
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
        "citation": item.url,
        "isBasedOn": item.url,
        "articleSection": item.trust_label_ko,
        "inLanguage": item.language or "ko-KR",
    }
    about = [link.franchise.name for link in item.franchise_links.all() if link.is_primary]
    primary_game_types = [link.franchise for link in item.franchise_links.all() if link.is_primary]
    mentioned_game_types = _mentioned_game_types(item, {franchise.slug for franchise in primary_game_types})
    if about:
        article_json_ld["about"] = about
    if item.published_at:
        article_json_ld["datePublished"] = item.published_at.isoformat()
    show_summary = bool(
        item.summary_ko
        and not is_generic_summary(item.summary_ko)
        and not item.title_suspect
        and item.content_type not in STATIC_CONTENT_TYPES
    )
    return render(
        request,
        "news/item_detail.html",
        {
            "item": item,
            "issue_link": issue,
            "related_items": related_items,
            "show_summary": show_summary,
            "display_summary": item.summary_ko if show_summary else fallback_summary_for(item),
            "public_excerpt": public_excerpt(item.raw_item.raw_text),
            "primary_game_types": primary_game_types,
            "mentioned_game_types": mentioned_game_types,
            "show_issue_debug": _show_internal_debug(request),
            "article_json_ld": json.dumps(article_json_ld, ensure_ascii=False),
            **_seo_context(
                request,
                canonical_url=canonical_url,
                description=_item_description(item),
            ),
        },
    )


def issue_list(request):
    now = timezone.now()
    issues = Issue.objects.annotate(
        item_count=Count(
            "news_links",
            filter=Q(
                news_links__news_item__is_archived=False,
                news_links__news_item__extraction_confidence__gte=LOW_CONFIDENCE_THRESHOLD,
                news_links__news_item__raw_item__rejection_reason="",
                news_links__news_item__is_date_suspect=False,
            )
            & (Q(news_links__news_item__published_at__isnull=True) | Q(news_links__news_item__published_at__lte=now)),
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
        issue.news_links.filter(
            news_item__is_archived=False,
            news_item__extraction_confidence__gte=LOW_CONFIDENCE_THRESHOLD,
            news_item__raw_item__rejection_reason="",
            news_item__is_date_suspect=False,
        )
        .filter(Q(news_item__published_at__isnull=True) | Q(news_item__published_at__lte=timezone.now()))
        .select_related("news_item", "news_item__source")
        .prefetch_related("news_item__franchise_links__franchise")
        .order_by(F("news_item__published_at").asc(nulls_last=True), "news_item__first_seen_at", "news_item__pk")
    )
    official_count = sum(1 for link in links if link.news_item.trust_label == "official")
    rumor_count = sum(1 for link in links if link.news_item.trust_label == "rumor")
    core_relations = {
        IssueRelation.SAME_STORY,
        IssueRelation.SOURCE_DUPLICATE,
        IssueRelation.FOLLOWUP,
        IssueRelation.CONFIRMATION,
        IssueRelation.OFFICIAL_CONFIRMATION,
        IssueRelation.DEBUNK,
        IssueRelation.CONTRADICTS,
    }
    core_links = [link for link in links if link.relation in core_relations]
    related_links = [link for link in links if link.relation == IssueRelation.RELATED]
    return render(
        request,
        "news/issue_detail.html",
        {
            "issue": issue,
            "links": links,
            "core_links": core_links,
            "related_links": related_links,
            "item_count": len(links),
            "official_count": official_count,
            "rumor_count": rumor_count,
            "show_issue_debug": _show_internal_debug(request),
            **_seo_context(
                request,
                canonical_url=_absolute_url(request, "news:issue_detail", issue.pk),
                description=f"{issue.title} 이슈의 관련 닌텐도 뉴스 {len(links)}건과 현재 상태를 확인합니다.",
            ),
        },
    )


def _mentioned_game_types(item: NewsItem, primary_slugs: set[str]) -> list[dict[str, str]]:
    seen = set(primary_slugs)
    mentioned: list[dict[str, str]] = []
    for mention in item.entity_mentions or []:
        slug = str(mention.get("slug") or "").strip()
        name = str(mention.get("name") or slug).strip()
        if not slug or slug in seen or mention.get("is_primary"):
            continue
        seen.add(slug)
        mentioned.append({"slug": slug, "name": name})
    return mentioned


def source_list(request):
    sources = Source.objects.annotate(
        item_count=Count("news_items", distinct=True),
        raw_count=Count("raw_items", distinct=True),
    ).order_by("name")
    for source in sources:
        source.public_last_error = _sanitize_public_error(source.last_error)
        source.status_info = _source_status(source)
    source_groups = _group_sources(list(sources))
    return render(
        request,
        "news/source_list.html",
        {
            "sources": sources,
            "source_groups": source_groups,
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
        "raw_items": RawItem.objects.count(),
        "public_items": _public_items_queryset().count(),
        "quarantined_items": NewsItem.objects.exclude(pk__in=_public_items_queryset().values("pk")).count(),
        "last_new": Source.objects.aggregate(total=Sum("last_new_items_count"))["total"] or 0,
        "status": "오류" if Source.objects.filter(enabled=True).exclude(last_error="").exists() else "주의" if Source.objects.filter(enabled=True).filter(Q(last_success_at__isnull=True) | Q(last_success_at__lt=stale_cutoff)).exists() else "정상",
        "recent_errors": Source.objects.filter(enabled=True).exclude(last_error="").count(),
        "last_updated_at": Source.objects.aggregate(last=Max("last_checked_at"))["last"],
    }
    for source in sources:
        source.public_last_error = _sanitize_public_error(source.last_error)
        source.status_info = _source_status(source)
    source_groups = _group_sources(list(sources))
    return render(
        request,
        "news/source_health.html",
        {
            "sources": sources,
            "source_groups": source_groups,
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
    franchises = Franchise.objects.annotate(item_count=Count("news_links", filter=Q(news_links__is_primary=True))).order_by("-priority", "name")
    return render(
        request,
        "news/franchise_list.html",
        {
            "franchises": franchises,
            **_seo_context(
                request,
                canonical_url=_absolute_url(request, "news:franchise_list"),
                description="Nintendo Watch에서 별칭으로 추적하는 닌텐도 게임종류 목록입니다.",
            ),
        },
    )


def franchise_favorites(request):
    if not request.user.is_authenticated:
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")
    franchises = list(Franchise.objects.annotate(item_count=Count("news_links", filter=Q(news_links__is_primary=True))).order_by("-priority", "name"))
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
        messages.success(request, "관심 게임종류를 저장했습니다.")
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
        .filter(franchise_links__is_primary=True)
        .select_related("source")
        .prefetch_related("franchise_links__franchise", "issue_links__issue")
        .distinct()
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
        {"loc": _absolute_url(request, "news:about"), "lastmod": None},
        {"loc": _absolute_url(request, "news:methodology"), "lastmod": None},
        {"loc": _absolute_url(request, "news:corrections"), "lastmod": None},
        {"loc": _absolute_url(request, "news:privacy"), "lastmod": None},
        {"loc": _absolute_url(request, "news:terms"), "lastmod": None},
    ]
    for item in _public_items_queryset().filter(published_at__isnull=False).select_related("source").order_by("-updated_at")[:1000]:
        urls.append({"loc": _absolute_url(request, "news:item_detail", item.pk), "lastmod": item.updated_at})
    for issue in (
        Issue.objects.filter(
            news_links__news_item__is_archived=False,
            news_links__news_item__raw_item__rejection_reason="",
            news_links__news_item__is_date_suspect=False,
        )
        .filter(Q(news_links__news_item__published_at__isnull=True) | Q(news_links__news_item__published_at__lte=timezone.now()))
        .distinct()
        .order_by("-last_updated_at")[:500]
    ):
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


STATIC_PAGE_CONTENT = {
    "about": {
        "title": "소개",
        "description": "Nintendo Watch는 비공식 닌텐도 뉴스 모니터링/아카이브입니다.",
        "sections": [
            ("무엇을 하는 곳인가요?", "Nintendo Watch는 공식 뉴스, 전문 매체 보도, 루머 출처를 모아 한국어로 빠르게 훑어볼 수 있게 정리하는 비공식 뉴스 모니터링 사이트입니다."),
            ("권리 고지", "Nintendo 및 관련 상표, 게임명, 캐릭터명은 각 권리자에게 있습니다. 이 사이트는 Nintendo와 제휴하거나 승인받은 서비스가 아닙니다."),
            ("운영", "운영자 연락처는 준비 중입니다. 수집은 출처별 설정에 따라 주기적으로 실행됩니다."),
        ],
    },
    "methodology": {
        "title": "방법론",
        "description": "출처, 중요도, 신뢰도, 이슈 묶음 기준을 설명합니다.",
        "sections": [
            ("출처 유형", "공식 출처, 전문 매체, 루머/유출 커뮤니티를 구분해 수집하고 표시합니다."),
            ("중요도와 신뢰도", "중요도는 뉴스 영향도와 관심도를, 신뢰도는 출처 성격과 공식 확인 여부를 나타냅니다. 루머는 중요도가 높아도 신뢰도는 낮게 표시될 수 있습니다."),
            ("자동 분류 한계", "제목, 요약, URL, 출처 메타데이터를 이용해 자동 분류하므로 오분류가 생길 수 있습니다. 의심 항목은 격리하거나 재검토 대상으로 남깁니다."),
        ],
    },
    "corrections": {
        "title": "정정과 요청",
        "description": "오분류, 삭제, 출처 추가, 정정 요청 안내입니다.",
        "sections": [
            ("정정 요청", "오분류, 잘못된 요약, 삭제 요청, 출처 추가 요청은 운영자 연락처가 준비되면 접수합니다."),
            ("정정 이력", "정정 이력 공개 영역은 준비 중입니다."),
        ],
    },
    "privacy": {
        "title": "개인정보처리방침",
        "description": "로그인, 쿠키, 개인화 기능에서 저장될 수 있는 정보를 설명합니다.",
        "sections": [
            ("저장 정보", "로그인 사용 시 계정 정보와 세션 정보가 저장될 수 있습니다. 읽음, 북마크, 관심 게임종류 기능을 사용하면 해당 선택이 저장됩니다."),
            ("쿠키와 세션", "로그인 유지와 CSRF 보호를 위해 쿠키와 세션을 사용합니다."),
            ("문의", "개인정보 문의처는 준비 중입니다."),
        ],
    },
    "terms": {
        "title": "이용약관",
        "description": "원문 저작권, 상표권, 자동 수집 정책과 책임 제한 안내입니다.",
        "sections": [
            ("원문 저작권", "원문 기사와 콘텐츠의 저작권은 각 권리자에게 있습니다. Nintendo Watch는 짧은 자체 요약과 원문 링크 중심으로 운영됩니다."),
            ("상표권", "Nintendo 및 관련 상표는 각 권리자에게 있습니다."),
            ("책임 제한", "자동 수집과 자동 분류 결과에는 오류가 있을 수 있으며, 루머/유출 정보는 공식 확인 전까지 사실로 간주하지 않습니다."),
        ],
    },
}


def static_page(request, slug: str):
    page = STATIC_PAGE_CONTENT.get(slug)
    if page is None:
        raise Http404("Page not found")
    return render(
        request,
        "news/static_page.html",
        {
            "static_page": page,
            **_seo_context(
                request,
                canonical_url=_absolute_url(request, f"news:{slug}"),
                description=page["description"],
            ),
        },
    )


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
