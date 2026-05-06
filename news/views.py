from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import NewsItemFilterForm
from .models import Franchise, Issue, NewsItem, Source
from .services.collectors import collect_source, process_raw_item, recalculate_news_item


PAGE_SIZE = 25


def home(request):
    return redirect("news:item_list")


def item_list(request):
    form = NewsItemFilterForm(request.GET or None)
    items = (
        NewsItem.objects.select_related("source")
        .prefetch_related("franchise_links__franchise", "issue_links__issue")
        .filter(is_archived=False)
    )

    if form.is_valid():
        data = form.cleaned_data
        if data.get("q"):
            query = data["q"]
            items = items.filter(
                Q(title__icontains=query)
                | Q(summary_ko__icontains=query)
                | Q(summary_original__icontains=query)
                | Q(source__name__icontains=query)
                | Q(raw_item__raw_text__icontains=query)
            )
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
            items = items.filter(is_bookmarked=True)
        if data.get("min_importance") is not None:
            items = items.filter(importance_score__gte=data["min_importance"])
        if data.get("date_from"):
            items = items.filter(Q(published_at__date__gte=data["date_from"]) | Q(first_seen_at__date__gte=data["date_from"]))
        if data.get("date_to"):
            items = items.filter(Q(published_at__date__lte=data["date_to"]) | Q(first_seen_at__date__lte=data["date_to"]))

    paginator = Paginator(items.distinct(), PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "news/item_list.html",
        {
            "form": form,
            "page": page,
            "query_params": query_params.urlencode(),
            "total_count": paginator.count,
        },
    )


def item_detail(request, pk: int):
    item = get_object_or_404(
        NewsItem.objects.select_related("source", "raw_item").prefetch_related(
            "franchise_links__franchise", "issue_links__issue"
        ),
        pk=pk,
    )
    issue = item.issue_links.select_related("issue").first()
    related_items = NewsItem.objects.none()
    if issue:
        related_items = (
            NewsItem.objects.filter(issue_links__issue=issue.issue)
            .exclude(pk=item.pk)
            .select_related("source")
            .order_by("-published_at", "-first_seen_at")[:20]
        )
    return render(
        request,
        "news/item_detail.html",
        {"item": item, "issue_link": issue, "related_items": related_items},
    )


def issue_list(request):
    issues = Issue.objects.annotate(item_count=Count("news_links")).order_by("-last_updated_at")
    paginator = Paginator(issues, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "news/issue_list.html", {"page": page, "total_count": paginator.count})


def issue_detail(request, pk: int):
    issue = get_object_or_404(Issue, pk=pk)
    links = (
        issue.news_links.select_related("news_item", "news_item__source")
        .prefetch_related("news_item__franchise_links__franchise")
        .order_by("-news_item__published_at", "-news_item__first_seen_at")
    )
    return render(request, "news/issue_detail.html", {"issue": issue, "links": links})


def source_list(request):
    sources = Source.objects.annotate(
        item_count=Count("news_items", distinct=True),
        raw_count=Count("raw_items", distinct=True),
    ).order_by("name")
    return render(request, "news/source_list.html", {"sources": sources})


def source_health(request):
    sources = Source.objects.annotate(
        item_count=Count("news_items", distinct=True),
        raw_count=Count("raw_items", distinct=True),
    ).order_by("enabled", "last_success_at", "name")
    totals = {
        "enabled": Source.objects.filter(enabled=True).count(),
        "errors": Source.objects.exclude(last_error="").count(),
        "items": NewsItem.objects.count(),
        "last_new": Source.objects.aggregate(total=Sum("last_new_items_count"))["total"] or 0,
    }
    return render(request, "news/source_health.html", {"sources": sources, "totals": totals})


def franchise_list(request):
    franchises = Franchise.objects.annotate(item_count=Count("news_links")).order_by("-priority", "name")
    return render(request, "news/franchise_list.html", {"franchises": franchises})


def franchise_detail(request, slug: str):
    franchise = get_object_or_404(Franchise, slug=slug)
    items = (
        NewsItem.objects.filter(franchise_links__franchise=franchise, is_archived=False)
        .select_related("source")
        .prefetch_related("franchise_links__franchise")
        .order_by("-published_at", "-first_seen_at")
    )
    paginator = Paginator(items, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "news/franchise_detail.html", {"franchise": franchise, "page": page})


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
    for raw_item in result.raw_items:
        _news_item, was_created = process_raw_item(raw_item)
        created += int(was_created)
    messages.success(
        request,
        f"{source.name}: 원본 {result.created_count}개, 뉴스 {created}개를 새로 만들었습니다.",
    )
    return redirect("news:source_list")


def _redirect_back(request, item: NewsItem):
    fallback = reverse("news:item_detail", args=[item.pk])
    return redirect(request.META.get("HTTP_REFERER") or fallback)
