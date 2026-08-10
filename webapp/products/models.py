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
