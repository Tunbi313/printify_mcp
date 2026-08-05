from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("templates/", views.template_gallery, name="template_gallery"),
    path("templates/<str:key>/images/", views.template_images, name="template_images"),
    path("upscale/", views.upscale_page, name="upscale_page"),
    path("batch/", views.batch_page, name="batch_page"),
    path("library/", views.library_page, name="library_page"),
    path("generate-title/", views.generate_title, name="generate_title"),
    path("generate-tags/", views.generate_tags, name="generate_tags"),
    path("create/", views.create_product, name="create_product"),
    path("product/<str:product_id>/", views.product_detail, name="product_detail"),
    path("product/<str:product_id>/publish/", views.publish_product, name="publish_product"),
    path("product/<str:product_id>/delete/", views.delete_product, name="delete_product"),
]
