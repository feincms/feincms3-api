# feincms3-api

A [django-ninja](https://django-ninja.dev/) API for reading and writing the
content of [feincms3](https://feincms3.readthedocs.io/) sites: pages and
other objects with
[django-content-editor](https://django-content-editor.readthedocs.io/)
regions and plugins,
[django-json-schema-editor](https://github.com/matthiask/django-json-schema-editor)
data and [django-cabinet](https://github.com/matthiask/django-cabinet) files.
For scripts, importers and agents, next to the Django admin, not instead of
it.

**Status:** alpha. The wire format and the Python API may still change.

The admin is the source of truth: the API finds, validates, saves and
permits exactly what the admin does, through the admin's querysets, forms,
formsets, save hooks and permissions. A plugin with custom validation, or a
project hooking `save_formset()`, behaves the same through the API without
any code for the API.

- [Integrating it](https://github.com/feincms/feincms3-api/blob/main/docs/integration.md) into a project: checking what's
  exposed, adapting it, running it.
- [How it's built](https://github.com/feincms/feincms3-api/blob/main/docs/architecture.md): the modules and where a project
  plugs in.
- [The spec](https://github.com/feincms/feincms3-api/blob/main/specs/api.md): the design and the reasoning behind it.
- [Issues](https://github.com/feincms/feincms3-api/issues): what's planned.
- [feincms3-api-cli](https://github.com/feincms/feincms3-api/blob/main/cli/README.md): the API from the command line
  (`f3api`), for people and agents with a shell.
- [Translating](https://github.com/feincms/feincms3-api/blob/main/docs/translating.md) content over the API.

## Setup

```bash
pip install feincms3-api
```

```python
INSTALLED_APPS = [
    ...,
    "feincms3_api",
]
```

Register what the API exposes; nothing else is. Each model needs a
`ModelAdmin`, that's where the forms, permissions and plugins come from:

```python
# app/content_api.py
from feincms3_api.api import ContentAPI
from feincms3_api.cabinet import FileResource, FolderResource
from feincms3_api.forms import ConfiguredFormResource
from feincms3_api.resources import ObjectResource, PageResource, TreeResource

from app.articles.models import Article
from app.forms.models import Form
from app.pages.models import Page

content_api = ContentAPI()
content_api.register(PageResource(Page))
content_api.register(ObjectResource(Article))
content_api.register(ConfiguredFormResource(Form))  # feincms3-forms
content_api.register(FileResource())  # django-cabinet
content_api.register(FolderResource())
```

Mount it, either as an API of its own or as a router in an existing
`NinjaAPI`:

```python
# app/urls.py
from app.content_api import content_api

urlpatterns = [
    ...,
    path("api/content/", content_api.as_api().urls),
]
```

`./manage.py migrate` creates the tables for tokens and uploads. The
interactive docs are at `api/content/docs`.

### Settings

```python
FEINCMS3_API = {
    # Never exposed: fields, or values inside JSON data
    "EXCLUDE_FIELDS": [
        "forms.form.recipient_email",
        "pages.jsonplugin_key_figures.data.internal_note",
    ],
    # What a string is, where its form field and name don't tell
    "KINDS": {"tipps.tipplist.color": "machine"},
    "MAX_BATCH_OPERATIONS": 100,
    "DEFAULT_PAGE_SIZE": 50,
    "MAX_PAGE_SIZE": 200,
    "TOKEN_LIFETIME": timedelta(hours=8),
    "UPLOAD_LIFETIME": timedelta(days=1),
    "UPLOAD_STORAGE": "default",
}
```

### The schema snapshot

`./manage.py f3api_schema app.content_api.content_api` prints every type
with its fields and what kind of value each is, as a superuser sees them.
Commit it and compare it in a test, so a new plugin or field becomes
visible through the API only after someone looked at the diff.

## Using it

Only active staff users can use the API, with their admin permissions.
Scripts and agents use tokens: staff users create their own in the admin
(the secret is shown once), or `./manage.py f3api_token USERNAME --write`
prints one. Tokens are read-only unless created for writing, and expire
after `TOKEN_LIFETIME`. `./manage.py f3api_cleanup` removes expired tokens
and uploads; run it daily.

```bash
API=https://example.com/api/content
AUTH="Authorization: Bearer f3api_…"

curl -H "$AUTH" $API/schema/                       # what's there
curl -H "$AUTH" "$API/pages/find/?path=/de/angebot/"
curl -H "$AUTH" "$API/pages/17/?expand=references"  # one page with its content
```

Writes need the ETag of what was read (`If-Match`), and fail as a whole with
every problem and its path. `?dry_run=true` runs a write in a transaction
which is rolled back and returns what it would have done:

```bash
curl -X PUT -H "$AUTH" -H 'If-Match: "9f2c…"' -H "Content-Type: application/json" \
  "$API/pages/17/content/?dry_run=true" \
  -d '{"content": {"main": [
        {"type": "pages.richtext", "fields": {"text": "<h2>Hello</h2>"}}
      ]}}'
```

**A dry run runs everything but the commit**, signal handlers included:
anything they do outside the database (sending mail, calling other APIs,
writing files) happens in a dry run too.

Files are uploaded first (`POST /uploads/`, multipart) and then named in a
write as `{"upload": "<id>"}`. Changes appear in the admin's history,
marked "(Through the API.)".

## Credits

feincms3-api grows out of feincms3-autotranslate, started by Moritz Wettstein
and Matthias Kestenholz at [Feinheit](https://www.feinheit.ch/) in October
2026. Its content introspection (which fields hold text, HTML or links, JSON
schemas, plugin discovery) is the base of this package.
