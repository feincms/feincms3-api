from langapp.models import Page as LanguagePage
from sitesapp.models import Page as SitePage

from feincms3_api.api import ContentAPI
from feincms3_api.resources import PageResource


multisite_api = ContentAPI()
multisite_api.register(PageResource(SitePage, name="site-pages"))
multisite_api.register(PageResource(LanguagePage, name="language-pages"))
