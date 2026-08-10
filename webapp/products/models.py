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


class AiVideo(models.Model):
    MODEL_CHOICES = [("kling", "Kling 2.5 Turbo Pro"), ("seedance", "Seedance 2.5")]

    product_id = models.CharField(max_length=64, db_index=True)
    product_title = models.CharField(max_length=255, blank=True)
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
