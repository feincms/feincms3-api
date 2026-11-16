from django.contrib import admin
from django.urls import path

from testapp.content_api import content_api
from testapp.multisite_api import multisite_api


api = content_api.as_api()
multisite = multisite_api.as_api(urls_namespace="multisite")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/multisite/", multisite.urls),
    path("api/", api.urls),
]
