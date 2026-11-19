from cabinet.fields import CabinetForeignKey
from content_editor.models import Region, create_plugin_base
from django import forms as django_forms
from django.conf import settings
from django.db import models
from django_json_schema_editor.plugins import JSONPluginBase
from django_prose_editor.fields import ProseEditorField
from feincms3.applications import ApplicationType, PageTypeMixin, TemplateType
from feincms3.mixins import (
    LanguageAndTranslationOfMixin,
    LanguageMixin,
    MenuMixin,
    RedirectMixin,
)
from feincms3.pages import AbstractPage
from feincms3_forms import models as forms_models, validation
from translated_fields import TranslatedField
from tree_queries.models import TreeNode


class Page(
    AbstractPage, PageTypeMixin, LanguageAndTranslationOfMixin, MenuMixin, RedirectMixin
):
    TYPES = [
        TemplateType(
            key="standard",
            title="standard",
            template_name="standard.html",
            regions=[Region(key="main", title="Main")],
        ),
        ApplicationType(
            key="articles",
            title="articles",
            urlconf="testapp.articles_urls",
            regions=[Region(key="main", title="Main")],
        ),
    ]
    MENUS = [("", "-"), ("main", "main")]

    lead = ProseEditorField(blank=True)

    class Meta(AbstractPage.Meta):
        unique_together = [("language_code", "translation_of")]


class Tag(models.Model):
    title = TranslatedField(models.CharField(max_length=100, blank=True))

    def __str__(self):
        return self.title


class Article(LanguageAndTranslationOfMixin):
    regions = [Region(key="main", title="Main")]
    is_active = models.BooleanField(default=True)
    title = models.CharField(max_length=200)
    slug = models.SlugField()
    lead = models.TextField(blank=True)
    tags = models.ManyToManyField(Tag, blank=True)
    teaser_page = models.ForeignKey(
        Page, on_delete=models.SET_NULL, blank=True, null=True, related_name="+"
    )

    class Meta:
        unique_together = [
            ("language_code", "translation_of"),
            ("language_code", "slug"),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f"/{self.language_code}/articles/{self.slug}/"


class TabList(models.Model):
    title = models.CharField(max_length=100)
    regions = [Region(key=code, title=name) for code, name in settings.LANGUAGES]

    def __str__(self):
        return self.title


class Tab(create_plugin_base(TabList)):
    title = models.CharField(max_length=100)
    text = ProseEditorField(blank=True)
    button_page = models.ForeignKey(
        Page, on_delete=models.SET_NULL, blank=True, null=True, related_name="+"
    )


class Form(forms_models.ConfiguredForm):
    FORMS = [
        forms_models.FormType(
            key="contact",
            label="contact",
            regions=[Region(key="form", title="form")],
            validate="testapp.models.validate_contact_form",
        )
    ]
    language_code = models.CharField(max_length=10, choices=settings.LANGUAGES)
    slot = models.CharField(
        max_length=20, blank=True, choices=[("personal", "personal")]
    )
    recipient_email = models.CharField(max_length=200, blank=True)
    success_message = models.TextField(blank=True)


class SimpleField(forms_models.SimpleFieldBase, create_plugin_base(Form)):
    pass


Text = SimpleField.proxy(SimpleField.Type.TEXT)
Select = SimpleField.proxy(SimpleField.Type.SELECT)
# No inline in the admin
Email = SimpleField.proxy(SimpleField.Type.EMAIL)


class FormRichText(create_plugin_base(Form)):
    """Not a field, between the fields"""

    text = ProseEditorField()


class Rating(forms_models.FormField, create_plugin_base(Form)):
    """A field type of the project's own, not a proxy"""

    max_stars = models.PositiveSmallIntegerField(default=5)

    def get_fields(self, **kwargs):
        return self.get_field(
            form_class=django_forms.IntegerField,
            min_value=1,
            max_value=self.max_stars,
            **kwargs,
        )


class Person(models.Model):
    name = models.CharField(max_length=100)
    role = TranslatedField(models.CharField(max_length=100, blank=True))
    portrait = CabinetForeignKey(
        on_delete=models.PROTECT, blank=True, null=True, related_name="+"
    )
    links = models.JSONField(blank=True, default=dict)

    def __str__(self):
        return self.name


class Training(LanguageMixin):
    is_active = models.BooleanField(default=True)
    title = models.CharField(max_length=200)
    slug = models.CharField(max_length=200)
    contents = ProseEditorField(blank=True)

    def __str__(self):
        return self.title


class Speaker(models.Model):
    training = models.ForeignKey(Training, on_delete=models.CASCADE)
    person = models.ForeignKey(Person, on_delete=models.CASCADE)

    def __str__(self):
        return str(self.person)


class Session(models.Model):
    training = models.ForeignKey(Training, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    subscription_url = models.URLField(blank=True)

    def __str__(self):
        return self.title


# Plugins ####################################################################

PagePlugin = create_plugin_base(Page)
ArticlePlugin = create_plugin_base(Article)


class RichText(PagePlugin):
    text = ProseEditorField()


class Button(PagePlugin):
    button_label = models.CharField(max_length=100)
    style = models.CharField(max_length=10, blank=True, choices=[("big", "big")])
    button_url = models.CharField(max_length=1000, blank=True)
    button_page = models.ForeignKey(
        Page, on_delete=models.CASCADE, blank=True, null=True, related_name="+"
    )


class FormContainer(PagePlugin):
    form = models.ForeignKey(Form, on_delete=models.PROTECT, related_name="+")


class TabListElement(PagePlugin):
    tab_list = models.ForeignKey(TabList, on_delete=models.CASCADE, related_name="+")


class Contact(PagePlugin):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="+")


class JSONPlugin(JSONPluginBase, PagePlugin):
    pass


KeyFigures = JSONPlugin.proxy(
    "key_figures",
    schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "figures": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "number": {"type": "number"},
                        "unit": {"type": "string"},
                    },
                },
            },
            "button_url": {"type": "string"},
        },
    },
)
Anchor = JSONPlugin.proxy(
    "anchor",
    schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "id": {"type": "string", "pattern": "^[-_A-Za-z0-9]+$"},
            "layout": {"type": "string", "enum": ["a", "b"]},
        },
    },
)
PageTeaser = JSONPlugin.proxy(
    "page_teaser",
    schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "page": {"type": "string", "format": "foreign_key"},
        },
    },
    foreign_key_paths={"testapp.page": ["page"]},
)


class Accordion(PagePlugin):
    container = True

    title = models.CharField(max_length=100)


class Download(PagePlugin):
    file = models.FileField(upload_to="downloads/")
    caption = models.CharField(max_length=200, blank=True)


class ArticleRichText(ArticlePlugin):
    text = ProseEditorField(
        extensions={
            "Bold": True,
            "Link": {"protocols": ["https", "mailto"]},
            "Heading": {"levels": [2, 3]},
        },
        sanitize=True,
    )


class ArticleButton(ArticlePlugin):
    button_label = models.CharField(max_length=100)
    button_page = models.ForeignKey(
        Page, on_delete=models.CASCADE, blank=True, null=True, related_name="+"
    )


class Textbook(models.Model):
    title = models.CharField(max_length=200)

    def __str__(self):
        return self.title


class Chapter(TreeNode):
    """A tree with content but without paths"""

    textbook = models.ForeignKey(
        Textbook, on_delete=models.CASCADE, related_name="chapters"
    )
    title = models.CharField(max_length=200)
    position = models.PositiveIntegerField(default=0)

    regions = [Region(key="main", title="Main")]

    class Meta:
        ordering = ["position"]

    def __str__(self):
        return self.title


class ChapterText(create_plugin_base(Chapter)):
    text = ProseEditorField()


def validate_contact_form(form):
    fields = form.get_formfields_union(plugins=[SimpleField, Rating])
    return [
        *validation.validate_uniqueness(fields),
        *validation.validate_required_fields(fields, {"email"}),
    ]
