from django.contrib.auth.decorators import login_required
from django.urls import path

from . import views

app_name = "news"


def private(view_func):
    return login_required(view_func)

urlpatterns = [
    path("", private(views.home), name="home"),
    path("items/", private(views.item_list), name="item_list"),
    path("items/<int:pk>/", private(views.item_detail), name="item_detail"),
    path("items/<int:pk>/mark-read/", private(views.mark_read), name="mark_read"),
    path("items/<int:pk>/toggle-bookmark/", private(views.toggle_bookmark), name="toggle_bookmark"),
    path("items/<int:pk>/archive/", private(views.archive_item), name="archive_item"),
    path("items/<int:pk>/recalculate/", private(views.recalculate_item), name="recalculate_item"),
    path("issues/", private(views.issue_list), name="issue_list"),
    path("issues/<int:pk>/", private(views.issue_detail), name="issue_detail"),
    path("sources/", private(views.source_list), name="source_list"),
    path("sources/<int:pk>/refresh/", private(views.refresh_source), name="refresh_source"),
    path("sources/health/", private(views.source_health), name="source_health"),
    path("franchises/", private(views.franchise_list), name="franchise_list"),
    path("franchises/<slug:slug>/", private(views.franchise_detail), name="franchise_detail"),
]
