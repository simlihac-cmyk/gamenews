from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("news", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="source",
            name="last_new_items_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
