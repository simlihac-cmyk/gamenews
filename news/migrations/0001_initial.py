# Generated for the Nintendo Watch MVP.

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Franchise",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("slug", models.SlugField(max_length=255, unique=True)),
                ("aliases", models.JSONField(blank=True, default=list)),
                ("priority", models.IntegerField(default=0)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="Issue",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=500)),
                ("canonical_topic", models.CharField(db_index=True, max_length=500)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("rumor", "Rumor"),
                            ("developing", "Developing"),
                            ("confirmed", "Confirmed"),
                            ("debunked", "Debunked"),
                            ("stale", "Stale"),
                        ],
                        default="developing",
                        max_length=32,
                    ),
                ),
                ("confidence_score", models.IntegerField(default=50)),
                ("first_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("official_confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-last_updated_at"],
                "indexes": [
                    models.Index(fields=["status", "last_updated_at"], name="news_issue_status_8dc113_idx"),
                    models.Index(fields=["first_seen_at"], name="news_issue_first_s_9e7a11_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="Source",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("slug", models.SlugField(max_length=255, unique=True)),
                ("url", models.URLField(blank=True, max_length=1000)),
                (
                    "source_type",
                    models.CharField(
                        choices=[
                            ("rss", "RSS/Atom"),
                            ("html", "HTML"),
                            ("youtube_rss", "YouTube RSS"),
                            ("reddit_rss", "Reddit RSS"),
                            ("google_alert_rss", "Google Alert RSS"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "trust_type",
                    models.CharField(
                        choices=[
                            ("official", "Official"),
                            ("press", "Press"),
                            ("rumor", "Rumor"),
                            ("aggregator", "Aggregator"),
                            ("unknown", "Unknown"),
                        ],
                        default="unknown",
                        max_length=32,
                    ),
                ),
                (
                    "region",
                    models.CharField(
                        choices=[
                            ("KR", "Korea"),
                            ("JP", "Japan"),
                            ("US", "United States"),
                            ("EU", "Europe"),
                            ("GLOBAL", "Global"),
                        ],
                        default="GLOBAL",
                        max_length=16,
                    ),
                ),
                (
                    "language",
                    models.CharField(
                        choices=[
                            ("ko", "Korean"),
                            ("ja", "Japanese"),
                            ("en", "English"),
                            ("multi", "Multiple"),
                        ],
                        default="multi",
                        max_length=16,
                    ),
                ),
                ("base_score", models.IntegerField(default=0)),
                ("enabled", models.BooleanField(default=True)),
                ("poll_interval_minutes", models.PositiveIntegerField(default=60)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("last_success_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("config", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["name"],
                "indexes": [
                    models.Index(fields=["enabled", "source_type"], name="news_source_enabled_40901d_idx"),
                    models.Index(fields=["trust_type", "region"], name="news_source_trust_t_5c8299_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="RawItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=500)),
                ("url", models.URLField(max_length=1000)),
                ("canonical_url", models.URLField(blank=True, max_length=1000, null=True)),
                ("author", models.CharField(blank=True, max_length=255)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("first_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("collected_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("raw_html", models.TextField(blank=True)),
                ("raw_text", models.TextField(blank=True)),
                ("content_hash", models.CharField(db_index=True, max_length=64)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "source",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="raw_items", to="news.source"),
                ),
            ],
            options={
                "ordering": ["-published_at", "-first_seen_at"],
                "indexes": [
                    models.Index(fields=["published_at"], name="news_rawite_publish_67cca0_idx"),
                    models.Index(fields=["first_seen_at"], name="news_rawite_first_s_3f2173_idx"),
                    models.Index(fields=["source"], name="news_rawite_source__934c24_idx"),
                    models.Index(fields=["content_hash"], name="news_rawite_content_dcf44f_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("canonical_url__isnull", False)) & ~models.Q(("canonical_url", "")),
                        fields=("canonical_url",),
                        name="unique_rawitem_canonical_url",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="NewsItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=500)),
                ("normalized_title", models.CharField(db_index=True, max_length=500)),
                ("url", models.URLField(max_length=1000)),
                ("canonical_url", models.URLField(blank=True, max_length=1000, null=True)),
                ("summary_ko", models.TextField(blank=True)),
                ("summary_original", models.TextField(blank=True)),
                (
                    "trust_label",
                    models.CharField(
                        choices=[
                            ("official", "Official"),
                            ("reported", "Reported"),
                            ("rumor", "Rumor"),
                            ("unknown", "Unknown"),
                        ],
                        default="unknown",
                        max_length=32,
                    ),
                ),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("official", "Official"),
                            ("direct", "Direct"),
                            ("release_date", "Release Date"),
                            ("trailer", "Trailer"),
                            ("new_game", "New Game"),
                            ("update", "Update"),
                            ("sale", "Sale"),
                            ("rumor", "Rumor"),
                            ("leak", "Leak"),
                            ("general", "General"),
                        ],
                        default="general",
                        max_length=32,
                    ),
                ),
                ("detected_tags", models.JSONField(blank=True, default=list)),
                ("confidence_score", models.IntegerField(default=50)),
                ("importance_score", models.IntegerField(default=0)),
                (
                    "region",
                    models.CharField(
                        choices=[
                            ("KR", "Korea"),
                            ("JP", "Japan"),
                            ("US", "United States"),
                            ("EU", "Europe"),
                            ("GLOBAL", "Global"),
                        ],
                        default="GLOBAL",
                        max_length=16,
                    ),
                ),
                (
                    "language",
                    models.CharField(
                        choices=[
                            ("ko", "Korean"),
                            ("ja", "Japanese"),
                            ("en", "English"),
                            ("multi", "Multiple"),
                        ],
                        default="multi",
                        max_length=16,
                    ),
                ),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("first_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("thumbnail_url", models.URLField(blank=True, max_length=1000)),
                ("is_read", models.BooleanField(default=False)),
                ("is_bookmarked", models.BooleanField(default=False)),
                ("is_archived", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "raw_item",
                    models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="news_item", to="news.rawitem"),
                ),
                (
                    "source",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="news_items", to="news.source"),
                ),
            ],
            options={
                "ordering": ["-published_at", "-first_seen_at", "-created_at"],
                "indexes": [
                    models.Index(fields=["trust_label", "category"], name="news_newsit_trust_l_cb222e_idx"),
                    models.Index(fields=["importance_score"], name="news_newsit_importa_284c8c_idx"),
                    models.Index(fields=["is_read", "is_bookmarked", "is_archived"], name="news_newsit_is_read_cafdb6_idx"),
                    models.Index(fields=["published_at"], name="news_newsit_publish_9ada03_idx"),
                    models.Index(fields=["first_seen_at"], name="news_newsit_first_s_efde82_idx"),
                    models.Index(fields=["source"], name="news_newsit_source__f60c8f_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("canonical_url__isnull", False)) & ~models.Q(("canonical_url", "")),
                        fields=("canonical_url",),
                        name="unique_newsitem_canonical_url",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="NewsItemFranchise",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "franchise",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="news_links", to="news.franchise"),
                ),
                (
                    "news_item",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="franchise_links", to="news.newsitem"),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("news_item", "franchise"), name="unique_newsitem_franchise")
                ]
            },
        ),
        migrations.CreateModel(
            name="NewsItemIssue",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "relation",
                    models.CharField(
                        choices=[
                            ("same_story", "Same story"),
                            ("followup", "Follow-up"),
                            ("confirmation", "Confirmation"),
                            ("debunk", "Debunk"),
                            ("related", "Related"),
                        ],
                        default="related",
                        max_length=32,
                    ),
                ),
                (
                    "issue",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="news_links", to="news.issue"),
                ),
                (
                    "news_item",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="issue_links", to="news.newsitem"),
                ),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("news_item", "issue"), name="unique_newsitem_issue")]
            },
        ),
        migrations.CreateModel(
            name="Notification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "channel",
                    models.CharField(
                        choices=[("ntfy", "ntfy"), ("discord", "Discord"), ("email", "Email"), ("none", "None")],
                        default="none",
                        max_length=32,
                    ),
                ),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("sent", "Sent"),
                            ("failed", "Failed"),
                            ("skipped", "Skipped"),
                        ],
                        default="pending",
                        max_length=32,
                    ),
                ),
                ("error", models.TextField(blank=True)),
                (
                    "news_item",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notifications", to="news.newsitem"),
                ),
            ],
            options={
                "ordering": ["-sent_at", "-id"],
                "indexes": [
                    models.Index(fields=["channel", "status"], name="news_notifi_channel_1214d1_idx"),
                    models.Index(fields=["news_item"], name="news_notifi_news_it_4db71f_idx"),
                ],
            },
        ),
    ]
