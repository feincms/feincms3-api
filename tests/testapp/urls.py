from django.contrib import admin
from django.urls import path

from testapp.content_api import content_api


api = content_api.as_api()

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
