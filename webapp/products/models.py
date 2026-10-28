from django.core.files.storage import default_storage
from django.db import models


class AiMockup(models.Model):
    KIND_CHOICES = [("scene", "Đổi bối cảnh"), ("model", "Người mẫu"), ("unknown", "Không rõ (ảnh cũ)")]

    product_id = models.CharField(max_length=64, db_index=True)
    product_title = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    label = models.CharField(max_length=255, blank=True)
    file_path = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def url(self) -> str:
        return default_storage.url(self.file_path)


class CrawlJob(models.Model):
    MODE_CHOICES = [
        ("single", "1 sản phẩm"),
        ("category", "Cả danh mục"),
        ("urls", "Danh sách URL"),
    ]
    STATUS_CHOICES = [("completed", "Hoàn tất"), ("failed", "Lỗi")]

    source_url = models.URLField(max_length=1000)
    mode = models.CharField(max_length=10, choices=MODE_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="completed")
    error_message = models.TextField(blank=True)
    vendor = models.CharField(max_length=255, blank=True)
    quantity = models.PositiveIntegerField(default=0)
    credits_used = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def product_count(self) -> int:
        return self.products.count()


class CrawledProduct(models.Model):
    job = models.ForeignKey(CrawlJob, related_name="products", on_delete=models.CASCADE)
    source_url = models.URLField(max_length=1000, blank=True)
    title = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)  # HTML, straight from the AI extraction
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    compare_at_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    sku = models.CharField(max_length=255, blank=True)
    barcode = models.CharField(max_length=255, blank=True)
    weight_grams = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    seo_title = models.CharField(max_length=255, blank=True)
    seo_description = models.CharField(max_length=500, blank=True)
    product_category = models.CharField(max_length=255, blank=True)
    product_type = models.CharField(max_length=255, blank=True)
    tags = models.JSONField(default=list, blank=True)
    images = models.JSONField(default=list, blank=True)
    variants = models.JSONField(default=list, blank=True)
    include_in_export = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    @property
    def thumb(self) -> str:
        return self.images[0] if self.images else ""


class CollectionExportState(models.Model):
    """Durable merge state for an asynchronous collection CSV export."""

    state_id = models.CharField(max_length=255, unique=True)
    collection_url = models.URLField(max_length=2000)
    product_urls = models.JSONField(default=list)
    fast_products_by_url = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class AiVideo(models.Model):
    MODEL_CHOICES = [
        ("kling", "Kling O3 Pro"),
        ("seedance", "Seedance 2.5"),
        ("ltx", "LTX Video 2.0 Fast"),
    ]
    TIER_CHOICES = [
        ("product_page", "Product Page (5s)"),
        ("homepage", "Homepage (10s)"),
        ("real_ad", "Real Ad (15s)"),
        ("story_ad", "Story Ad (15-20s+)"),
        ("custom", "Tùy chỉnh"),
    ]

    # product_id/product_title are set only when the source image came from
    # an existing Printify product — Video AI no longer requires one
    # (direct image upload is the primary path), so both may be blank.
    product_id = models.CharField(max_length=64, blank=True, db_index=True)
    product_title = models.CharField(max_length=255, blank=True)
    source_image = models.URLField(max_length=1000, blank=True)
    duration_tier = models.CharField(max_length=20, choices=TIER_CHOICES, blank=True, default="custom")
    prompt_model = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=10, choices=MODEL_CHOICES)
    prompt = models.TextField(blank=True)
    duration = models.CharField(max_length=4)
    aspect_ratio = models.CharField(max_length=10)
    cost_usd = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    file_path = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def url(self) -> str:
        return default_storage.url(self.file_path)
