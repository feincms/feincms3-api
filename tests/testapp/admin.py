from content_editor.admin import ContentEditor, ContentEditorInline, deny_regions
from django.contrib import admin
from django_json_schema_editor.plugins import JSONPluginInline
from feincms3.admin import TreeAdmin
from feincms3_forms.admin import (
    ConfiguredFormAdmin,
    FormFieldInline,
    SimpleFieldInline,
)

from testapp import models


@admin.register(models.Page)
class PageAdmin(ContentEditor, TreeAdmin):
    list_display = [*TreeAdmin.list_display, "is_active"]
    search_fields = ["title"]
    readonly_fields = ["path"]
    inlines = [
        ContentEditorInline.create(models.RichText),
        ContentEditorInline.create(models.Button),
        ContentEditorInline.create(models.FormContainer),
        ContentEditorInline.create(models.TabListElement),
        ContentEditorInline.create(models.Contact),
        ContentEditorInline.create(models.Accordion),
        ContentEditorInline.create(models.Download),
        JSONPluginInline.create(models.KeyFigures),
        # Allowed nowhere: Page has no other region than main
        JSONPluginInline.create(models.Anchor, regions=deny_regions({"main"})),
        JSONPluginInline.create(models.PageTeaser),
    ]


@admin.register(models.Article)
class ArticleAdmin(ContentEditor):
    search_fields = ["title"]
    inlines = [
        ContentEditorInline.create(models.ArticleRichText),
        ContentEditorInline.create(models.ArticleButton),
    ]


@admin.register(models.TabList)
class TabListAdmin(ContentEditor):
    inlines = [ContentEditorInline.create(models.Tab)]


@admin.register(models.Training)
class TrainingAdmin(admin.ModelAdmin):
    pass


admin.site.register(models.Tag)
admin.site.register(models.Person)


@admin.register(models.Form)
class FormAdmin(ConfiguredFormAdmin):
    inlines = [
        SimpleFieldInline.create(models.Text),
        SimpleFieldInline.create(models.Select),
        ContentEditorInline.create(models.FormRichText),
        FormFieldInline.create(
            models.Rating,
            core_fields=[*FormFieldInline.core_fields, "max_stars"],
        ),
    ]


@admin.register(models.Chapter)
class ChapterAdmin(ContentEditor, TreeAdmin):
    list_display = [*TreeAdmin.list_display, "textbook"]
    inlines = [ContentEditorInline.create(models.ChapterText)]


admin.site.register(models.Textbook)
