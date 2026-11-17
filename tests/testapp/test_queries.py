from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext

from testapp.test_write import WriteTestCase


# Every request loads the session and the user.
REQUEST = 2
# Pages have ten plugin inlines on eight tables.
PLUGIN_INLINES = 10


class QueryCountTest(WriteTestCase):
    def count(self, fn):
        # Cached for the process, so it would depend on the tests run before
        ContentType.objects.clear_cache()
        with CaptureQueriesContext(connection) as ctx:
            response = fn()
        self.assertLess(response.status_code, 300, response.content)
        return len(ctx.captured_queries)

    def test_detail(self):
        # The page, its plugins per inline, the hidden plugins in one query
        with self.assertNumQueries(REQUEST + 1 + PLUGIN_INLINES + 1):
            self.get(f"pages/{self.angebot.pk}/")

    def test_list(self):
        # The pages and their count
        with self.assertNumQueries(REQUEST + 2):
            self.get("pages/")

    def test_list_with_content(self):
        # The plugins are fetched once per inline for all pages, only the
        # hidden plugins are counted per page.
        one, three = (
            self.count(
                lambda limit=limit: self.get(f"pages/?content=true&limit={limit}")
            )
            for limit in (1, 3)
        )
        self.assertEqual(one, REQUEST + 2 + PLUGIN_INLINES + 1)
        self.assertEqual(three - one, 2)

    def test_writes(self):
        # Pinned, to notice changes; most are the admin's formsets and the
        # tree's saves.
        path = f"pages/{self.angebot.pk}/"
        etag = self.etag(path)
        self.assertEqual(
            self.count(
                lambda: self.send("patch", path, {"fields": {"title": "X"}}, etag=etag)
            ),
            71,
        )
        etag = self.etag(path)
        self.assertEqual(
            self.count(
                lambda: self.send(
                    "patch",
                    f"{path}plugins/testapp.button/{self.button.pk}/",
                    {"fields": {"button_label": "Los"}},
                    etag=etag,
                )
            ),
            74,
        )
