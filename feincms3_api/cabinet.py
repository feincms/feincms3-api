"""
django-cabinet: files and folders

Files go through the file model's registered admin like any other resource;
their file fields take uploads (``{"upload": id}``). Folders have no admin
of their own in cabinet (the file admin edits them), so they get one here,
with cabinet's form and the folder model's permissions, as in cabinet.
"""

from cabinet.base_admin import FolderForm
from cabinet.models import Folder, get_file_model
from django.contrib import admin
from django.urls import NoReverseMatch, reverse
from django.utils.functional import cached_property

from feincms3_api.errors import ConflictError
from feincms3_api.resources import ObjectResource


class FileResource(ObjectResource):
    filter_fields = ("folder",)

    def __init__(self, **kwargs):
        super().__init__(get_file_model(), **kwargs)

    def url(self, request, obj):
        try:
            url = obj.file.url
        except ValueError:
            return None
        return request.build_absolute_uri(url)

    def describe(self, request, obj):
        description = super().describe(request, obj) | {
            "name": obj.file_name,
            "size": obj.file_size,
        }
        if getattr(obj, "image_file", None):
            description |= {
                "width": obj.image_width,
                "height": obj.image_height,
                "alt_text": obj.image_alt_text,
            }
        return description


class FolderAdmin(admin.ModelAdmin):
    form = FolderForm
    fields = ["parent", "name"]
    search_fields = ["name"]


class FolderResource(ObjectResource):
    filter_fields = ("parent",)

    def __init__(self, **kwargs):
        super().__init__(Folder, **kwargs)

    @cached_property
    def model_admin(self):
        return FolderAdmin(Folder, self.admin_site)

    def admin_url(self, obj):
        try:
            return reverse(
                f"{self.admin_site.name}:cabinet_folder_change", args=[obj.pk]
            )
        except NoReverseMatch:
            return None

    def check_delete(self, request, obj):
        if (
            obj.children.exists()
            or get_file_model()._default_manager.filter(folder=obj).exists()
        ):
            raise ConflictError("The folder isn't empty.")
