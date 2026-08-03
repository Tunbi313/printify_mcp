from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("create/", views.create_product, name="create_product"),
    path("product/<str:product_id>/", views.product_detail, name="product_detail"),
    path("product/<str:product_id>/publish/", views.publish_product, name="publish_product"),
    path("product/<str:product_id>/delete/", views.delete_product, name="delete_product"),
]
