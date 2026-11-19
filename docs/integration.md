# Integrating feincms3-api into a project

How to add the API to an existing feincms3 site, check what it exposes,
and adapt it where the defaults don't fit. The README has the short
version of the setup; [How it's built](architecture.md) explains the
modules this guide refers to.

## What the API needs from the project

The API has no configuration of its own for fields, validation or
permissions: it uses the admin. Before registering a model, check that:

- **The model has a `ModelAdmin`** on the admin site (`admin.site`, or the
  one passed as `admin_site=`). Without one, the system check
  `feincms3_api.E001` fails.
- **Plugins are `ContentEditorInline`s** of django-content-editor. The
  inlines decide which plugin types exist and in which regions they're
  allowed (`regions=`). Other inlines (`TabularInline` and so on) are
  exposed as `inlines`, per type.
- **No two inlines of an admin use the same model** (`feincms3_api.E003`):
  the API identifies inlines by their model, so it can't tell them apart.
- **Validation is in the forms**, in `clean()` methods and the model's
  `clean()`, not in the admin's JavaScript. What the admin's forms and
  formsets accept, the API accepts.
- **Saving logic is in the admin's hooks** (`save_model()`,
  `save_related()`, `save_formset()`) or in the models. The API calls the
  same hooks in the same order as the change view.

django-prose-editor, django-json-schema-editor, django-cabinet,
feincms3-forms, feincms3-sites and feincms3-language-sites are supported
without extra code.

## Install and mount

```bash
pip install feincms3-api
```

Add `"feincms3_api"` to `INSTALLED_APPS` and run `./manage.py migrate`
(tokens and uploads). Then register the models in a module of the project:

```python
# app/content_api.py
from feincms3_api.api import ContentAPI
from feincms3_api.resources import ObjectResource, PageResource

from app.articles.models import Article
from app.pages.models import Page

content_api = ContentAPI()
content_api.register(PageResource(Page))
content_api.register(ObjectResource(Article))
```

Mount it as its own `NinjaAPI`, with the interactive docs at `…/docs`:

```python
# app/urls.py
(path("api/content/", content_api.as_api().urls),)
```

Or, if the project has a `NinjaAPI` already, as a router in it. The router
brings its own authentication (`ContentAPI(auth=...)`, tokens and staff
sessions by default):

```python
api.add_router("/content/", content_api.router)
```

The API answers paths below its prefix which match nothing with a JSON
`404` (naming the resources, for a mistyped one) rather than the site's
404 page. URLs mounted below the API's prefix (`api/content/other/`) go
before it in `urlpatterns`.

## Choosing resources

Only registered models are exposed. Pick the resource class by what the
model is:

| Model | Resource |
|---|---|
| feincms3 pages | `PageResource`: paths, `find/?path=`, moving by `parent` and `position`, feincms3-sites and feincms3-language-sites |
| other django-tree-queries trees (chapters, categories) | `TreeResource`: a `parent` filter, deleting nodes with children is refused |
| anything else, with or without plugins | `ObjectResource` |
| django-cabinet | `FileResource()`, `FolderResource()` from `feincms3_api.cabinet` |
| feincms3-forms `ConfiguredForm` | `ConfiguredFormResource` from `feincms3_api.forms`: the form type's validation in `meta.warnings` |

Every resource takes these arguments:

- `name=`: the URL segment, by default the model's type
  (`app_label.model_name`, e.g. `app.article`), which is unique. Set it
  when you prefer another (`ObjectResource(TabList, name="tab-lists")`);
  letters, digits, `_`, `.` and `-`.
- `filters=`: the fields lists can be filtered by, replacing the class's
  `filter_fields` (`language_code`, `is_active`, `translation_of` and
  `site`, where the model has them, plus `parent`, `page_type` and `path`
  for trees and pages). `?q=` searches with the admin's `search_fields`;
  without them it's an error, so give the admins of resources clients look
  for (pages!) `search_fields`. `/schema/` lists each resource's `filters`
  and whether it can be searched (`search`).
- `required_on_create=`: fields a create must give although the model has a
  default. `PageResource` requires `is_active`, so a client decides whether
  a new page is published.
- `admin_site=`: the admin site to take the `ModelAdmin` from.

## Checking what's exposed

Check this before the API goes live, and again whenever models or admins
change.

**System checks** run with `./manage.py check` and in every test run:
`E001` a resource's model without an admin, `E002` an `EXCLUDE_FIELDS`
entry which doesn't exist, `E003` two inlines with the same model, `E004` a
`KINDS` entry which doesn't exist or names no kind.

**The schema snapshot** lists every type the API exposes, with its fields
and what kind of value each is, as a superuser sees them:

```bash
./manage.py f3api_schema app.content_api.content_api > app/content_api_snapshot.txt
```

```text
app.article (resource)
  is_active: boolean
  title: text
  slug: machine
  teaser_page: ref app.page
  region main: app.richtext, app.button
app.button (plugin of app.page)
  button_label: text
  button_url: link
```

Commit it, and compare it in a test, so that a new plugin or field becomes
visible through the API only after someone has looked at the diff:

```python
from pathlib import Path
from io import StringIO

from django.core.management import call_command
from django.test import TestCase


class ContentAPISnapshotTest(TestCase):
    def test_snapshot(self):
        out = StringIO()
        call_command("f3api_schema", "app.content_api.content_api", stdout=out)
        snapshot = Path(__file__).parent / "content_api_snapshot.txt"
        self.assertEqual(out.getvalue(), snapshot.read_text())
```

Go through it line by line once:

- **Is anything there that shouldn't be?** Internal notes, recipient email
  addresses, API keys in plugins. Exclude them in the settings; this works
  for values inside JSON data too:

  ```python
  FEINCMS3_API = {
      "EXCLUDE_FIELDS": [
          "forms.form.recipient_email",
          "pages.jsonplugin_key_figures.data.internal_note",
      ],
  }
  ```

- **Is the kind right?** The kinds tell translators and agents what to do
  with a string: `text` and `html` are translated, `link` is mapped to the
  other language's counterpart, `machine` is kept. Kinds come from the form
  field (`URLField` is a link, `SlugField` machine) and from the name (`url`,
  `link`, `*_url` and `*_link` are links, `ppoi` and `*_ppoi` machine). A
  `CharField` holding a color, a CSS class or an inline SVG is `text` unless
  told otherwise, per field:

  ```python
  FEINCMS3_API = {
      "KINDS": {
          "projects.customer.svg": "machine",
          "tipps.tipplist.color": "machine",
          "pages.jsonplugin_teaser.data.items[].target": "link",
      },
  }
  ```

  For a form field class of the project's own, register a codec instead;
  see [custom form fields](#custom-form-fields) below.
- **Is something missing?** Lines like `excluded: color (no codec for
  ColorFormField)` are form fields the API doesn't know. It leaves them out
  instead of guessing; register a codec for them.

## Admin behaviour to keep in mind

- **Permissions** are the admin's: `has_view_permission()`,
  `has_change_permission()` and so on, for the model and for each inline.
  `get_queryset()` decides which objects a user finds. Read-only fields
  (`get_readonly_fields()`) are read-only in the API as well.
- **Fields** are the admin form's (`get_form()`, `fields`, `fieldsets`,
  `exclude`). A field missing from the admin form is missing from the API.
- **Dry runs** run everything but the commit, signal handlers and save
  hooks included. Anything they do outside the database (sending mail,
  calling other services, writing files, clearing caches) happens in a dry
  run too. Use `transaction.on_commit()` for such side effects; that's
  correct for the admin as well.
- **History**: writes create admin log entries, marked "(Through the
  API.)", with the user the token belongs to.

## Adapting the API

The hooks, from the most to the least common.

### Resource hooks

Subclass the resource class and register the subclass:

```python
from feincms3_api.errors import ConflictError
from feincms3_api.resources import ERROR, PageResource, warning


class Pages(PageResource):
    def check(self, request, obj):
        """Problems which don't stop saving, in ``meta.warnings``"""
        warnings = list(super().check(request, obj))
        if obj.page_type == "standard" and not obj.meta_description:
            warnings.append(
                warning("No meta description.", path="fields.meta_description")
            )
        return warnings

    def check_delete(self, request, obj):
        if obj.is_landing_page:
            raise ConflictError("Landing pages can't be deleted.")
        super().check_delete(request, obj)

    def describe(self, request, obj):
        """What ``?expand=references`` shows for a page"""
        return {**super().describe(request, obj), "page_type": obj.page_type}

    def cache_keys(self, request, obj):
        """What ``DELETE /resources/pages.page/{id}/cache/`` deletes"""
        return [*super().cache_keys(request, obj), f"navigation-{obj.language_code}"]


content_api.register(Pages(Page))
```

- `check()`: the object is saved, but it may not work as intended: a form
  without a recipient, a page type without required content. Use
  `level=ERROR` for "won't work until fixed". Clients see these after every
  read and every write, dry runs included.
- `validate_instance()`: errors that should stop saving but that the admin
  forms don't find, returned as `[{"path", "message"}]`. Prefer putting the
  validation into the admin form, where editors get it too.
- `check_delete()`: raise `ConflictError` to refuse a deletion.
- `describe()`: how references to the model are shown to clients.
- `cache_keys()`: the cache keys of the object's rendered content. The
  default is django-content-editor's key for
  `regions_from_item(..., timeout=...)`; add the keys of what the project
  caches itself, so clients can see their changes on the site at once.

### Custom form fields

A form field the API doesn't know needs a codec, registered by form field
class (subclasses are found along the MRO):

```python
from feincms3_api.codecs import KIND, Codec, HTMLCodec
from feincms3_api.kinds import MACHINE

from app.fields import ColorFormField, RichTextFormField


class ColorCodec(Codec):
    def json_schema(self, field):
        return {"type": "string", "pattern": "^#[0-9a-f]{6}$", KIND: MACHINE}


content_api.codecs.register(ColorFormField, ColorCodec())
content_api.codecs.register(RichTextFormField, HTMLCodec())
```

A codec has four methods, all optional: `to_wire()` (the value clients
read), `from_wire()` (the value the form field's `clean()` gets),
`json_schema()` (with the kind for strings), and `references()` (the
`(type, id)` of objects the value refers to). See `feincms3_api/codecs.py`
for the built-in ones.

### Adapters

An adapter decides a type's `fields` as a whole, not field by field. It's
only needed when the admin form is a poor shape for clients: fields a form
builds in `__init__` from other data, JSON stored in a `TextField`, values
set in `save_model()` that shouldn't be written directly. Subclass
`feincms3_api.adapters.FormAdapter` and register it per type:

```python
content_api.adapters.register("app.mapplugin", MapAdapter())
```

So far the default adapter has covered every type; if you need one, please
open an issue, it may point to something the API should handle itself.

## Running it

- **Tokens**: staff users create their own in the admin (the secret is
  shown once), with the model permissions as everywhere in the admin:
  grant `feincms3_api.view_apitoken`, `add_apitoken`, `change_apitoken`
  and `delete_apitoken` to the users or groups who should have tokens
  (with django-authlib's roles, a glob like `feincms3_api.*`). Superusers
  have them anyway and also manage everyone else's tokens. Taking the
  permissions away doesn't revoke tokens a user already has; delete them
  in the admin, or let them expire. `./manage.py f3api_token USERNAME --write` prints one for a
  script. Tokens act as their user, are read-only unless created for
  writing, and expire after `TOKEN_LIFETIME` (8 hours by default). Users
  list and revoke their own tokens over the API (`GET /tokens/`, `DELETE
  /tokens/{id}/`, `DELETE /tokens/current/`; `f3api tokens`, `f3api
  sites logout`). The routes come with the API. For scripts and agents, create a staff user with only the permissions they
  need rather than using a superuser's token.
- **Cleanup**: run `./manage.py f3api_cleanup` daily; it removes expired
  tokens and uploads. Set it up wherever the servers' scheduled jobs live
  (a crontab, a systemd timer, a Kubernetes `CronJob`), which often isn't
  the project's repository.
- **Basic auth gates**: a staging site behind basic auth (django-curtains'
  `basic_auth`, or the web server's) blocks every token: both use the
  `Authorization` header, and a request can only send one. Exempt the
  API's path from the gate; the API authenticates every request itself.
  With django-curtains:

  ```python
  BASIC_AUTH_EXEMPT = r"^/api/content/"
  ```

- **Dependencies**: install a released version for deployments. An
  editable checkout (`[tool.uv] sources` with a `path`) is handy while
  integrating, but a container build can't reach it.
- **Uploads** are staged in `UPLOAD_STORAGE` (the default storage) for
  `UPLOAD_LIFETIME` (a day) until a write uses them.
- **Clients**: the [CLI](../cli/README.md) (`uvx feincms3-api-cli`) is the
  easiest way in for people and agents with a shell. The OpenAPI schema at
  `…/openapi.json` is typed, so client generators produce real types.
- **Translating**: see [Translating](translating.md).

## Checklist

- [ ] Every registered model has a `ModelAdmin`; `./manage.py check` passes.
- [ ] The snapshot is committed and compared in a test.
- [ ] Secrets and internal fields are in `EXCLUDE_FIELDS`.
- [ ] Kinds are right (`KINDS` where they aren't); no unexpected
      `excluded:` lines.
- [ ] Side effects of saving use `transaction.on_commit()`.
- [ ] `cache_keys()` covers what the project caches.
- [ ] `f3api_cleanup` runs daily on every server.
- [ ] Basic auth gates exempt the API's path.
- [ ] Deployments install a released version, not a local checkout.
- [ ] Scripts and agents have their own users with limited permissions.
