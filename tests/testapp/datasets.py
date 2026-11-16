from feincms3_api.incubator.autotranslate.datasets import (
    language_regions_dataset,
    object_dataset,
    page_tree_dataset,
)
from testapp.models import Article, Form, Page, Session, Speaker, TabList, Training


def datasets():
    return {
        "pages": page_tree_dataset(Page, language_regions=[TabList]),
        "articles": object_dataset(Article),
        "tablists": language_regions_dataset(TabList),
        "trainings": object_dataset(Training, related=[Speaker, Session]),
        "forms": object_dataset(Form, offline={"slot": ""}, title_field="name"),
    }
