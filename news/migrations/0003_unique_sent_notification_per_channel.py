from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("news", "0002_source_last_new_items_count"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="notification",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "sent")),
                fields=("news_item", "channel"),
                name="unique_sent_notification_per_channel",
            ),
        ),
    ]
