from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0007_aivideo_duration_tier_aivideo_prompt_model_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="CollectionExportState",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("state_id", models.CharField(max_length=255, unique=True)),
                ("collection_url", models.URLField(max_length=2000)),
                ("product_urls", models.JSONField(default=list)),
                ("fast_products_by_url", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
