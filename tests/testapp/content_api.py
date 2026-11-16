from feincms3_api.api import ContentAPI
from feincms3_api.cabinet import FileResource, FolderResource
from feincms3_api.resources import ObjectResource, PageResource
from testapp.models import Article, Page, Person, TabList, Training


content_api = ContentAPI()
content_api.register(PageResource(Page))
content_api.register(ObjectResource(Article))
content_api.register(ObjectResource(TabList, name="tab-lists"))
content_api.register(ObjectResource(Training))
content_api.register(ObjectResource(Person))
content_api.register(FileResource())
content_api.register(FolderResource())
