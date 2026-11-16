from content_editor.admin import ContentEditor, ContentEditorInline
from django.contrib import admin
from feincms3.admin import TreeAdmin

from langapp import models


@admin.register(models.Page)
class PageAdmin(ContentEditor, TreeAdmin):
    list_display = [*TreeAdmin.list_display]
    inlines = [ContentEditorInline.create(models.RichText)]
