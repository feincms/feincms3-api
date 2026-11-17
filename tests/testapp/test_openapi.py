from django.contrib.auth.models import Permission, User
from django.test import SimpleTestCase
from openapi_spec_validator import validate

from feincms3_api.api import ContentAPI
from feincms3_api.openapi import component_name, rebase
from feincms3_api.resources import PageResource
from testapp.models import Page
from testapp.test_write import WriteTestCase


SCHEMAS = "#/components/schemas/"


class OpenAPITest(WriteTestCase):
    def openapi(self, prefix=""):
        response = self.client.get(f"/api/{prefix}openapi.json")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_valid(self):
        validate(self.openapi())
        validate(self.openapi("multisite/"))

    def test_routes(self):
        paths = self.openapi()["paths"]

        def schema(operation, status=None):
            if status is None:
                content = operation["requestBody"]["content"]
            else:
                content = operation["responses"][status]["content"]
            return content["application/json"]["schema"]["$ref"]

        detail = paths["/api/pages/{pk}/"]
        self.assertEqual(schema(detail["get"], "200"), f"{SCHEMAS}TestappPage")
        self.assertEqual(schema(detail["get"], "404"), f"{SCHEMAS}Error")
        self.assertEqual(schema(detail["patch"]), f"{SCHEMAS}TestappPageWrite")
        self.assertEqual(schema(detail["patch"], "412"), f"{SCHEMAS}Error")
        self.assertEqual(
            schema(paths["/api/pages/"]["get"], "200"), f"{SCHEMAS}TestappPageList"
        )
        self.assertEqual(
            schema(paths["/api/pages/{pk}/content/"]["put"]),
            f"{SCHEMAS}TestappPageContentWrite",
        )
        self.assertEqual(schema(paths["/api/batch/"]["post"]), f"{SCHEMAS}Batch")
        plugin = paths["/api/pages/{pk}/plugins/{type_label}/{plugin_id}/"]
        self.assertEqual(schema(plugin["patch"]), f"{SCHEMAS}PluginWrite")
        self.assertEqual(schema(plugin["patch"], "200"), f"{SCHEMAS}TestappPagePlugin")

    def test_components(self):
        schemas = self.openapi()["components"]["schemas"]
        page = schemas["TestappPage"]
        self.assertEqual(page["properties"]["type"], {"const": "testapp.page"})
        self.assertEqual(page["properties"]["id"], {"type": "integer"})
        self.assertEqual(
            page["properties"]["content"]["$ref"], f"{SCHEMAS}TestappPageContent"
        )
        self.assertEqual(
            schemas["TestappPageList"]["properties"]["items"]["items"]["$ref"],
            f"{SCHEMAS}TestappPage",
        )
        # The fields are what /schema/{type}/ says for a superuser
        fields = self.get("schema/testapp.button/").json()
        del fields["$schema"]
        self.assertEqual(schemas["TestappButtonFields"], fields)

        # Plugins per region, by their type
        main = schemas["TestappPageContent"]["properties"]["main"]["items"]
        self.assertEqual(main["discriminator"]["propertyName"], "type")
        self.assertEqual(
            main["discriminator"]["mapping"]["testapp.button"],
            f"{SCHEMAS}TestappButton",
        )
        # Anchors aren't allowed in main
        self.assertNotIn("testapp.jsonplugin_anchor", main["discriminator"]["mapping"])
        self.assertIn(
            "testapp.jsonplugin_anchor",
            schemas["TestappPagePlugin"]["discriminator"]["mapping"],
        )
        button = schemas["TestappButton"]
        self.assertEqual(button["properties"]["type"], {"const": "testapp.button"})
        self.assertEqual(
            button["properties"]["children"]["items"]["$ref"],
            f"{SCHEMAS}TestappPagePlugin",
        )

        # Other inlines
        training = schemas["TestappTraining"]["properties"]["inlines"]
        self.assertEqual(
            training["properties"]["testapp.session"]["items"]["$ref"],
            f"{SCHEMAS}TestappSession",
        )

    def test_superset(self):
        # Types a user can't see are in the OpenAPI schema all the same
        user = User.objects.create_user("staff", "", "staff", is_staff=True)
        user.user_permissions.add(
            Permission.objects.get(
                codename="view_page", content_type__app_label="testapp"
            )
        )
        self.client.force_login(user)
        self.assertEqual(self.get("schema/testapp.richtext/").status_code, 404)
        schemas = self.openapi()["components"]["schemas"]
        self.assertIn("TestappRichtext", schemas)
        self.assertNotIn(
            "readOnly", schemas["TestappPageFields"]["properties"]["title"]
        )

    def test_validation(self):
        # The bodies are validated as before, the rest is the operations'
        self.assertEqual(self.send("post", "pages/", [1]).status_code, 422)
        self.assertEqual(self.send("post", "pages/", {"fields": "x"}).status_code, 422)
        response = self.send(
            "post", "pages/", {"fields": {"title": "X"}, "unknown": 1}, dry_run="true"
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("errors", response.json())


class ComponentsTest(SimpleTestCase):
    def test_lazy(self):
        api = ContentAPI()
        api.register(PageResource(Page))
        api.router  # noqa: B018
        self.assertEqual(api.components._builders, {})

    def test_component_name(self):
        self.assertEqual(
            component_name("testapp.jsonplugin_key_figures"),
            "TestappJsonpluginKeyFigures",
        )

    def test_rebase(self):
        schema = {
            "$defs": {"item": {"type": "string"}},
            "properties": {"items": {"items": {"$ref": "#/$defs/item"}}},
            "x-other": {"$ref": "https://example.com/schema"},
        }
        self.assertEqual(
            rebase(schema, "#/components/schemas/XFields/"),
            {
                "$defs": {"item": {"type": "string"}},
                "properties": {
                    "items": {
                        "items": {"$ref": "#/components/schemas/XFields/$defs/item"}
                    }
                },
                "x-other": {"$ref": "https://example.com/schema"},
            },
        )
