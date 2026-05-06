from django.contrib.auth.decorators import login_required
from django.urls import path

from . import views

app_name = "news"

urlpatterns = [
    path("", views.home, name="home"),
    path("items/", views.item_list, name="item_list"),
    path("items/<int:pk>/", views.item_detail, name="item_detail"),
    path("items/<int:pk>/mark-read/", login_required(views.mark_read), name="mark_read"),
    path("items/<int:pk>/toggle-bookmark/", login_required(views.toggle_bookmark), name="toggle_bookmark"),
    path("items/<int:pk>/archive/", login_required(views.archive_item), name="archive_item"),
    path("items/<int:pk>/recalculate/", login_required(views.recalculate_item), name="recalculate_item"),
    path("issues/", views.issue_list, name="issue_list"),
    path("issues/<int:pk>/", views.issue_detail, name="issue_detail"),
    path("sources/", views.source_list, name="source_list"),
    path("sources/<int:pk>/refresh/", login_required(views.refresh_source), name="refresh_source"),
    path("sources/health/", views.source_health, name="source_health"),
    path("sources/health/internal/", views.source_health_internal, name="source_health_internal"),
    path("franchises/", views.franchise_list, name="franchise_list"),
    path("franchises/favorites/", views.franchise_favorites, name="franchise_favorites"),
    path("franchises/<slug:slug>/", views.franchise_detail, name="franchise_detail"),
    path("sitemap.xml", views.sitemap_xml, name="sitemap_xml"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
]
