from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("templates/", views.template_gallery, name="template_gallery"),
    path("templates/<str:key>/images/", views.template_images, name="template_images"),
    path("upscale/", views.upscale_page, name="upscale_page"),
    path("studio/", views.ai_studio_page, name="ai_studio_page"),
    path("studio/restyle/", views.studio_restyle, name="studio_restyle"),
    path("studio/remove-bg/", views.studio_remove_bg, name="studio_remove_bg"),
    path("studio/resize/", views.studio_resize, name="studio_resize"),
    path("batch/", views.batch_page, name="batch_page"),
    path("library/", views.library_page, name="library_page"),
    path("mockup-studio/", views.mockup_studio_index, name="mockup_studio_index"),
    path("mockup-studio/gallery/", views.mockup_gallery, name="mockup_gallery"),
    path("mockup-studio/gallery/<int:mockup_id>/delete/", views.mockup_delete, name="mockup_delete"),
    path("video-studio/", views.video_studio_index, name="video_studio_index"),
    path("video-studio/gallery/", views.video_gallery, name="video_gallery"),
    path("video-studio/gallery/<int:video_id>/delete/", views.video_delete, name="video_delete"),
    path("generate-title/", views.generate_title, name="generate_title"),
    path("generate-tags/", views.generate_tags, name="generate_tags"),
    path("create/", views.create_product, name="create_product"),
    path("product/<str:product_id>/", views.product_detail, name="product_detail"),
    path("product/<str:product_id>/mockup/", views.mockup_studio, name="mockup_studio"),
    path("product/<str:product_id>/video/", views.video_studio, name="video_studio"),
    path("product/<str:product_id>/video/generate-prompts/", views.video_generate_prompts, name="video_generate_prompts"),
    path("product/<str:product_id>/publish/", views.publish_product, name="publish_product"),
    path("product/<str:product_id>/delete/", views.delete_product, name="delete_product"),
]
