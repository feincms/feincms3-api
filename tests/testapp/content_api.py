from feincms3_api.api import ContentAPI
from feincms3_api.cabinet import FileResource, FolderResource
from feincms3_api.forms import ConfiguredFormResource
from feincms3_api.resources import ObjectResource, PageResource, TreeResource
from testapp.models import (
    Article,
    Chapter,
    Form,
    Page,
    Person,
    TabList,
    Textbook,
    Training,
)


content_api = ContentAPI()
content_api.register(PageResource(Page))
content_api.register(ObjectResource(Article))
content_api.register(ObjectResource(TabList, name="tab-lists"))
content_api.register(ObjectResource(Training))
content_api.register(ObjectResource(Person))
content_api.register(ConfiguredFormResource(Form))
content_api.register(ObjectResource(Textbook))
content_api.register(TreeResource(Chapter))
content_api.register(FileResource())
content_api.register(FolderResource())
