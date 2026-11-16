from content_editor.models import Region, create_plugin_base
from django.db import models
from feincms3.applications import PageTypeMixin, TemplateType
from feincms3_language_sites.models import AbstractPage


class Page(AbstractPage, PageTypeMixin):
    """A feincms3-language-sites page: paths are unique per language"""

    TYPES = [
        TemplateType(
            key="standard",
            title="standard",
            template_name="standard.html",
            regions=[Region(key="main", title="Main")],
        ),
    ]

    class Meta(AbstractPage.Meta):
        pass


class RichText(create_plugin_base(Page)):
    text = models.TextField()
