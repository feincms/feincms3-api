from content_editor.admin import ContentEditor, ContentEditorInline, deny_regions
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
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


class SessionFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        titles = [
            form.cleaned_data.get("title")
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        ]
        if len(titles) != len(set(titles)):
            raise ValidationError("Two sessions have the same title.")


class SpeakerInline(admin.TabularInline):
    model = models.Speaker
    extra = 0


class SessionInline(admin.StackedInline):
    model = models.Session
    formset = SessionFormSet
    extra = 0


@admin.register(models.Training)
class TrainingAdmin(admin.ModelAdmin):
    inlines = [SpeakerInline, SessionInline]

    def save_formset(self, request, form, formset, change):
        # A project's own hook: sessions get the training's URL by default
        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, models.Session) and not instance.subscription_url:
                instance.subscription_url = f"https://example.com/{form.instance.slug}/"
            instance.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()


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
