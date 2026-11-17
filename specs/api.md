# feincms3-api: design

How the API works and why it works that way. How the code is organised is
in [docs/architecture.md](../docs/architecture.md); what's planned is in the
[issues](https://github.com/feincms/feincms3-api/issues).

A django-ninja API for reading and writing the content of feincms3 sites:
pages and other objects with django-content-editor regions and plugins,
django-json-schema-editor data and django-cabinet files. For scripts,
importers and agents (e.g. Claude Code with a token and `curl`), next to the
admin, not instead of it.

It grows out of feincms3-autotranslate (see "Credits"): its content
introspection is this package's core, and its translation pipeline lives in
an incubator subpackage (see "Translating"). Ideas taken from it: explicit
allowlists, a committed snapshot of what's exposed, dry runs in a
rolled-back transaction, errors as report lines instead of tracebacks.

## Why

- **Importers.** Relaunches move content from old sites into feincms3. Today
  that's a one-off script per project against the models. An API with
  validation is the same script without knowing the plugin models.
- **Bulk edits.** Meta descriptions for 200 pages, a renamed product in every
  rich text, a link that moved. Scriptable, reviewable with a dry run.
- **Agents.** Drafting a page from a briefing, translating a page, finding
  unused files or pages without a lead. The schema endpoint tells the agent
  what's allowed where.
- **Reports.** Inactive pages, empty regions, plugins of a type nobody uses.

## Prior art: Wagtail 8.0

Wagtail 8.0 (2026-08-25) ships a **preview v3 API** that can write: pages,
images, documents, snippets, revisions, workflows, 50+ admin operations. It's
opt-in (`wagtail.api.v3` in `INSTALLED_APPS`, URLs mounted by the project),
built on **Django Ninja and Pydantic**, with OpenAPI docs, bearer tokens and
Wagtail's permission model. The pitch is "CMS with AI, not AI CMS": no AI
buttons in the admin, but an API agents can use, with `wagtail-cli` and
`wagtail-mcp` on top. Rich text can be read as Markdown.

Shape worth copying:

- `GET|POST /pages/`, `GET|PATCH|DELETE /pages/{id}/`, actions as
  `POST /pages/{id}/actions/{publish,move,copy,…}/`,
- `GET /schema/` and `GET /schema/{type}/`: the content types and their
  create/patch/read schemas,
- StreamField as a list of `{"id", "type", "value"}` blocks,
- `limit`/`offset` pagination, lists as `{"count", "items"}`.

What doesn't carry over: Wagtail's StreamField blocks are declared in one
place with one schema system. feincms3 plugins are Django models, each
project has its own, and the admin inlines decide which plugin goes into
which region. Wagtail also has drafts, revisions and workflows; feincms3 has
`is_active` (see "Drafts").

## The central decision: the admin is the source of truth

Every feincms3 site already describes its content in the admin: which
templates and page types there are, which regions they have, which plugins
are allowed in which region (`ContentEditorInline.create(model,
regions=[…])`, `allow_regions`, `deny_regions`), which fields an editor sees
(`fields`, `fieldsets`, `exclude`, `readonly_fields`), how they're validated
(form `clean()` methods, `NoembedValidationForm`, prose-editor sanitising,
json-schema-editor's `fastjsonschema` check) and who may do what
(`has_*_permission`, `get_queryset`).

The API reuses all of that instead of describing it a second time:

- **Writes go through the admin's forms and hooks.** `ModelAdmin.get_form(
  request, obj)` for the object, and for every inline (plugins and others)
  the formset `inline.get_formset()` returns, with the admin's prefixes and
  querysets and a form for every row, as a POST from the change view would
  have. The wire values are turned into form data, the forms and formsets
  run exactly the validation the admin runs (formset `clean()`s and the
  check for protected objects included), and saving goes through
  `save_form()`, `save_model()`, `save_related()` and so `save_formset()`,
  logged with `construct_change_message()`. A plugin with custom validation
  or a project hooking `save_formset()` behaves the same through the API
  and in the admin, without the project writing anything for the API.
- **Permissions and visibility** come from the `ModelAdmin` and its inlines:
  `get_queryset(request)` decides what can be found at all,
  `has_view/add/change/delete_permission(request, obj)` what can be done. A
  role which can't see some configuration in the admin can't see it through
  the API either.
- **Regions and allowed plugins** come from the object (`obj.regions`, from
  its template or page type) and the `ContentEditor`'s inlines.

Why it matters: model fields alone don't say enough. django-json-schema-
editor's model `JSONField` doesn't validate `data` against the schema (only
the references); that happens in the form field. Noembed checks and every
project's form `clean()` live in forms too (prose-editor sanitises in both
the model and the form field, so that one would survive). An API
validating with `full_clean()` and its own Pydantic models would accept what
the admin rejects, and the next editor opening the page gets the error.

Rejected: **Pydantic models generated from the model fields plus
`full_clean()`** (misses all of the above and duplicates the admin
configuration), **Django REST Framework** (django-ninja is what Wagtail's
new API uses, and gives typed endpoints and OpenAPI with less code).

The cost: the admin forms expect a request, and admin code sometimes assumes
a browser (messages, redirects, `request.POST`). Called are `get_form`,
`get_inline_instances` and `get_formset`, `get_queryset`, the permission
methods, and the save path of the change view (`save_form`, `save_model`,
`save_related`, `save_formset`, `construct_change_message`, `log_*`);
`delete_model` for deletions. Hooks reading `request.POST` (content-editor's
cloning, feincms3-forms' validation messages) see an empty one; the forms'
validation reaches the API as `meta.warnings` through `Resource.check()`
instead.

## What is exposed: resources

Nothing is exposed unless the project registers it: the registration is the
one place a reviewer can see what can be read and changed through the API.
Form submissions, users, orders stay out by construction.

```python
# app/content_api.py
from feincms3_api.api import ContentAPI
from feincms3_api.cabinet import FileResource, FolderResource
from feincms3_api.resources import ObjectResource, PageResource

from articles.models import Article
from events.models import Event
from pages.models import Page
from tabs.models import TabList


content_api = ContentAPI()
content_api.register(PageResource(Page))
content_api.register(ObjectResource(Article))
content_api.register(ObjectResource(Event))
content_api.register(ObjectResource(TabList, title_field="technical_title"))
content_api.register(FileResource())
content_api.register(FolderResource())

# app/api.py: a NinjaAPI of the project's own, or content_api.as_api()
api.add_router("/content/", content_api.router)
```

A resource needs the model to be registered in the admin site
(`admin_site=` if it isn't the default one); that's where its forms,
permissions and plugins come from. A model with plugins but no
`ContentEditor` admin is refused at startup.

| Resource | for | adds |
|---|---|---|
| `ObjectResource` | any model, with or without plugins | nothing; articles, blog entries, events, tab lists |
| `ConfiguredFormResource` | feincms3-forms `ConfiguredForm` subclasses | the form type's validation in `meta.warnings` |
| `TreeResource` | django-tree-queries `TreeNode` subclasses | `parent` filter, moved by changing `parent`, deleting a node with children is refused; chapters of a textbook, categories |
| `PageResource` | feincms3 `AbstractPage` subclasses | a `TreeResource` with `path`, `find/` and sites |
| `FileResource`, `FolderResource` | django-cabinet's file model and folders | folder filter; folders get an admin of their own (cabinet has none), with cabinet's form and the folder permissions |

Pages are one content model among several: the tests also cover articles,
feincms3-forms forms (proxy plugins of one table per field type, next to
plugins of their own, and form types as region sets) and chapters, a tree
without paths. Which plugins an object has is what the inlines' querysets
show, as in the admin.

Objects with a region per language (a tab list shared between the
languages, with regions `de`, `fr`, …, rendering the current language's
region) need nothing special: their regions are regions.

## The wire format

One object, as `GET /content/pages/17/` returns it:

```json
{
  "type": "pages.page",
  "id": 17,
  "fields": {
    "title": "Angebot",
    "slug": "angebot",
    "parent": {"type": "pages.page", "id": 3},
    "position": 20,
    "page_type": "standard",
    "language_code": "de",
    "is_active": true,
    "lead": "<p>…</p>",
    "header_image": {"type": "cabinet.file", "id": 412},
    "meta_description": "…"
  },
  "meta": {
    "path": "/de/angebot/",
    "url": "https://example.com/de/angebot/",
    "admin_url": "/admin/pages/page/17/change/",
    "etag": "\"9f2c…\"",
    "regions": [{"key": "main", "title": "Main", "inherited": false}],
    "warnings": []
  },
  "content": {
    "main": [
      {"type": "pages.richtext", "id": 101, "depth": 0,
       "fields": {"text": "<h2>…</h2><p>…</p>", "is_highlighted": false}},
      {"type": "pages.accordion", "id": 104, "depth": 0,
       "fields": {"title": "Fragen"}},
      {"type": "pages.richtext", "id": 105, "depth": 1,
       "fields": {"text": "<p>Antwort</p>", "is_highlighted": false}},
      {"type": "pages.jsonplugin_key_figures", "id": 102, "depth": 0,
       "fields": {"data": {"title": "…", "figures": [{"title": "…"}]}}},
      {"type": "pages.button", "id": 103, "depth": 0,
       "fields": {"button_label": "Kontakt", "button_url": "",
                  "button_page": {"type": "pages.page", "id": 44}}}
    ]
  }
}
```

- **`type`** is the model's `label_lower`. JSON plugin proxies use the
  proxy's label (`pages.jsonplugin_key_figures`), as autotranslate's
  `--list-fields` does, so each has its own schema; so do feincms3-forms'
  field proxies (`forms.simplefield_text`).
- **`fields`** are the admin form's fields, in wire format (below).
  `region`, `ordering` and `parent` of plugins never appear: they follow
  from where the plugin is in `content`.
- **`content`** maps region keys to plugin lists in order, flat, each with
  its `depth`: the plugins following a container plugin
  (django-content-editor 10, `container = True`) with a greater depth are
  inside it, as in the database. Flat because it's what scripts filter and
  map over most easily, and what a write needs anyway. `?nested=true` on
  reads returns containers with their plugins in `"children": […]` instead
  (and no `depth`), for clients that want the tree. Writes take the flat
  form only.
- **Plugins in regions the object doesn't have** (after a page type change)
  are returned under their region key and listed in `meta.warnings`; the
  admin keeps them too.
- **`meta.warnings`** are problems which don't stop saving, each
  `{"level", "message", "path"}`: `level` is `warning` or `error` (saved,
  but it won't work until fixed), `path` is left out when the problem isn't
  in one place. Besides the content's own (hidden plugins, unknown regions),
  a resource adds its own in `Resource.check(request, obj)`;
  `ConfiguredFormResource` adds the feincms3-forms form type's validation,
  which the admin shows as messages. Every read has them, so a write's
  response (dry runs included) tells what the change left to fix. The
  plugin `PATCH` returns the plugin with the object's `etag` and `warnings`
  in its `meta`.
- **References** are `{"type", "id"}` objects on read; on write a bare id is
  accepted where the field has one target model. References inside
  django-json-schema-editor data (`format: foreign_key`) stay bare ids, as
  stored; the schema marks them (`x-feincms3-ref`) where the plugin's
  `foreign_key_paths` say where they are.
- **`?expand=references`** (detail, list, `find/`, plugin) adds
  `meta.references`, `{type: {id: description}}` of every reference in the
  object's fields, plugins and inlines, JSON data included, so a reader
  doesn't need one request per reference:

  ```json
  "references": {
    "pages.page": {"17": {"title": "Angebot", "path": "/de/angebot/",
                          "url": "…", "admin_url": "…"}},
    "cabinet.file": {"4": {"title": "…", "name": "anna.png", "size": 2048,
                           "width": 40, "height": 20, "alt_text": "…",
                           "url": "…", "admin_url": "…"}}
  }
  ```

  The references themselves aren't touched, so what was read can still be
  written back, and the ETag stays the same. Resources describe their
  objects (`Resource.describe()`, also the start of their `meta`), other
  models in the admin get their title and admin URL. What the user may not
  see, or what doesn't exist, is `null`. A list looks its references up
  together, not per item.
- **Paths** inside an object use autotranslate's notation:
  `content.main[2].fields.data.figures[0].title`. Error paths, the schema
  snapshot and exclusions in the settings all use it.

## Field codecs and plugin adapters

Plugins aren't standardised: each project has its own. That's handled in two
layers, both registries with built-in entries and room for the project's
own.

**Codecs** turn one form field into wire format and back, and describe it as
JSON Schema for `/schema/`. They're looked up by form field class along the
MRO, so a project's subclass of a prose-editor field uses the prose-editor
codec without being registered.

| form field | wire | schema |
|---|---|---|
| `CharField` | string | `maxLength`; the kind from the name and format |
| `URLField`, `EmailField` | string | `format: uri`, `email`; links |
| `ChoiceField`, `TypedChoiceField`, `MultipleChoiceField` | string(s) | `enum` |
| `BooleanField`, `IntegerField`, `FloatField` | as JSON | type, bounds |
| `DecimalField` | string, to keep the precision | `format: decimal` |
| date, time, date and time, duration fields | ISO 8601 string | `format` |
| `ModelChoiceField`, `ModelMultipleChoiceField` (cabinet's `CabinetForeignKey` included) | reference(s) | the target type (`x-feincms3-ref`), nullable |
| django-prose-editor field | HTML string | `contentMediaType: text/html`; the allowed markup (`x-feincms3-html`) |
| django-json-schema-editor `JSONEditorField` | the JSON value | the field's own schema with kinds; its foreign keys marked as references |
| `JSONField` | the JSON value | none |
| `FileField`, `ImageField` (the object's own file) | `{"name", "url"}`; written as `{"upload": id}` | `x-feincms3-file` |

**Allowed markup.** A django-prose-editor field configured with
`extensions` knows which tags, attributes and URL schemes they produce; its
schema says so in `x-feincms3-html`: prose-editor's nh3 allowlist (`tags`,
`attributes` per tag, `url_schemes`, `allowed_classes`, … as far as the
extensions set them) and `sanitized`, whether the form or model field
removes everything else (`sanitize=True`). Without sanitising, the
allowlist describes the editor, not what the server enforces. Fields in
prose-editor's legacy mode (no `extensions`) have no `x-feincms3-html`.

**Text for people.** Every string in the schema also says what it is
(`feincms3_api.kinds`, from autotranslate's `fields.py`): `"x-feincms3-kind"`
is `text`, `html`, `link`, `machine` (slugs, paths, choices, enums, anything
with a `pattern`, feincms3-forms field names) or `choices` (a feincms3-forms
choice list, `value|label` lines of which only the labels are text). That's
what a translating agent (see "Translating") or a spell checker needs to
know, and the snapshot shows it.

A form field without a codec isn't guessed at: the field is left out, and
`/schema/` and the snapshot list it as excluded, naming the form field
class. The project registers a codec or excludes the field explicitly.

**Adapters** decide a plugin's (or object's) `fields` as a whole. The
default `FormAdapter` is the admin form's fields through the codecs, and
covers every plugin and object made of ordinary fields without any code:
feincms3's plugins and mixins, json-schema-editor proxies, a project's
button plugin with `button_label`, `button_url` and `button_page`. An
adapter has `fields()`, `to_wire()`, `form_data()` (the current values with
the wire values decoded over them) and `json_schema()`; a project registers
one for a type (`content_api.adapters.register("pages.button", …)`) only to
change the shape: grouping fields into one object, exposing a list or JSON
stored in a `TextField` as such, hiding a value `save_model()` sets. No
adapter beyond the default is built until a project needs one.

**The snapshot.** `./manage.py f3api_schema [dotted.path.to.api]` prints
every type with its fields and their kinds, as a superuser sees them, like
autotranslate's `--list-fields`. The project commits it as a snapshot test:
a new plugin or field becomes visible through the API only after someone
has looked at the diff.

```
pages.button (plugin of pages.page)
  button_label: text
  button_url: link
  button_page: ref pages.page
pages.jsonplugin_key_figures (plugin of pages.page)
  data: json
  data.title: text
  data.figures[].title: text
  data.figures[].number: number
pages.page (resource pages)
  parent: ref pages.page
  page_type: choice
  title: text
  lead: html
  path: machine (read-only)
  excluded: header_video (no codec for VideoField)
  region standard.main: pages.richtext, pages.button, …
  region landing.hero: pages.teaser
```

**Read-only fields** are the admin's `readonly_fields`, marked `readOnly` in
the schema and returned like the others. A user who may only view an object
gets every field read-only, as the admin shows it.

## Endpoints

Under the project's mount point, `{resource}` being the resource's name
(`pages`, `articles`, …, default: the model name with an `s`, or `name=`;
not the verbose name, which changes with the language):

| | |
|---|---|
| `GET /schema/` | resources, their types, regions per template/page type, allowed plugins per region, container plugins |
| `GET /schema/{type}/` | JSON Schema of one type's `fields`, for reading and for writing |
| `GET /{resource}/` | list: `limit`, `offset`, `q` (the admin's `search_fields`), filters declared on the resource (default: `language_code`, `is_active`, `translation_of`, `site`; pages also `parent`, `page_type`, `path`), `fields=` to thin out, `content=true`, `expand=references` |
| `POST /{resource}/` | create, with or without `content` |
| `GET /{resource}/{id}/` | one object with its content |
| `PATCH /{resource}/{id}/` | change fields; `content` if given, see below |
| `PUT /{resource}/{id}/content/` | replace the content of the regions given |
| `GET /{resource}/{id}/plugins/{type}/{plugin_id}/` | one plugin |
| `PATCH /{resource}/{id}/plugins/{type}/{plugin_id}/` | change one plugin's fields |
| `DELETE /{resource}/{id}/` | delete |
| `DELETE /{resource}/{id}/cache/` | delete the cached rendering, see below |
| `GET /pages/find/?path=/de/angebot/` | resolve a path, also with host for multisite |
| `POST /uploads/` | upload a file (multipart), returns an upload id, see "Files" |
| `POST /batch/` | several writes in one transaction, see "Batches" |
| `GET|POST /files/`, `GET|PATCH|DELETE /files/{id}/` | cabinet, see below |
| `GET|POST /folders/`, `GET|PATCH|DELETE /folders/{id}/` | cabinet folders |

**OpenAPI.** Next to `/schema/`, the OpenAPI schema types every route for
generated clients: a component per type (`TestappPage`, its `…Fields`,
`…Write`, `…List`, `…Content`, a component per plugin and inline type, the
plugins of a resource as a `oneOf` by `type` per region), and `Error` for
the error responses. The components are built from the same JSON schemas
as `/schema/{type}/`, as a superuser sees them, when the OpenAPI schema is
first requested (per language, since titles are translated), not at
startup. So they're a superset of what a user sees: the admin's per-user
hooks (`get_fields()`, `get_readonly_fields()`, …) and inline permissions
can hide or lock fields, and choices may be limited per user or site. We
assume users with access to a model and its inlines are trusted with all
of its fields; where they aren't, a write's `422` says so, and
`/schema/{type}/` is exact for each user. The routes' bodies are still
validated by the operations only, not by the components, so errors keep
their paths. One component per plugin serves reading and writing: `id` is
left out for new plugins, read-only fields are `readOnly`.

Lists return `{"count", "items"}`, items without `content` unless
`?content=true`. List filters are declared on the resource (`filters=`),
not taken from the admin's `list_filter`: its entries can be arbitrary
`SimpleListFilter`s, and the admin's use case (narrowing a list for a
person) isn't the API's.

## Writing content

`PUT /{resource}/{id}/content/` with `{"content": {"main": […]}}`:

- **Regions given are replaced, others are left alone.** An agent rewriting
  `main` doesn't have to round-trip the sidebar.
- A plugin **with `id`** is that plugin, updated. It must belong to this
  object and region set and have the same `type`; changing the type means a
  new plugin.
- A plugin **without `id`** is created.
- A plugin of a replaced region **not in the list** is deleted.
- Order comes from the list (`ordering` 10, 20, …), `depth` from each
  plugin (default `0`). The depths are checked, not repaired: the first
  plugin is at depth 0, and a plugin can only be one deeper than a
  container before it which is still open.
- Each plugin must be allowed in its region by its inline, and the user
  needs the inline's add/change/delete permission for what happens to it.

This is what the admin does on save: all inlines in one request. The plugin
`PATCH` is for the small edits (one link, one typo) without sending the whole
region; it goes through the same formsets and hooks.

**Other inlines.** Inlines which aren't plugins (a training's sessions, a
`TabularInline` of anything) are `inlines` on the wire, by type, each object
`{"id", "fields"}` in the formset's order:

```json
"inlines": {
  "events.session": [{"id": 4, "fields": {"title": "Morning"}}]
}
```

Writing works as with regions: a type given in `inlines` is replaced (with
`id` changed, without created, left out deleted), types not given are left
alone. Errors of the formset's own `clean()` are at `inlines.events.session`.
`/schema/` lists each resource's `inlines`, `/schema/{type}/` their fields.

Every write is one transaction. All forms are validated first, and all
errors are collected before anything is saved, so a request fails as a whole
with every problem listed:

```json
HTTP 422
{"errors": [
  {"path": "content.main[3].fields.data.figures[0].title",
   "message": "This field is required."},
  {"path": "content.main[5]",
   "message": "pages.formcontainer isn't allowed in region main."}
]}
```

**Dry run.** `?dry_run=true` on every write runs it, including the saves,
inside a transaction that's rolled back, and returns the object as it would
be (new plugins with `"id": null`). Its result is what the write will do,
not a prediction. Signal handlers doing things outside the database (mail,
APIs, files) run in a dry run too; the docs must say so.

**Concurrent edits.** feincms3 objects have no revision counter, and editors
keep using the admin. `GET` returns an `ETag`, a hash of the object's
fields, content and inlines in wire format. Every `PATCH`, `PUT` and `DELETE` needs
`If-Match` (`428` without, `412` if the object changed since), the plugin
`PATCH` included, with the object's ETag. The admin doesn't check anything,
so an admin save can still overwrite an API write; the other direction is
covered.

**Cached rendering.** Sites often cache rendered regions
(`regions_from_item(..., timeout=60)`), so a client checking its edit on
the site would have to wait for the timeout. `DELETE …/cache/` deletes the
object's cache keys (`Resource.cache_keys()`, by default
django-content-editor's `regions-{type}-{id}`), with the permission to
change the object. It's explicit, not done on every write: only a client
looking at the result needs it. A site with its own `cache_key=` overrides
`cache_keys()`; pages inheriting regions from their ancestors keep the
ancestors' old content until their own keys are deleted too.

## Operations without HTTP

The endpoints are thin: under them are the operations, methods of
`ContentAPI` taking a request (the admin's methods need one) and wire data
(`list()`, `detail()`, `create()`, `update()`, `replace_content()`,
`update_plugin()`, `delete()`, `clear_cache()`, `batch()`), which do the form handling, permission
checks, validation and logging. The ninja endpoints only parse the request,
call one operation and map its result or errors to a response.

Two reasons: the logic is testable without HTTP, and work that spans many
objects can run in one transaction in process. A subtree is many creates,
and its dry run and its load should each be one `atomic()`; over HTTP that's
many requests. Management commands (autotranslate, importers) call the
operations directly; over HTTP, `POST /batch/` does the same.

### Batches

`POST /batch/` runs several writes in one transaction, all of them or
none, and `?dry_run=true` rolls all of them back:

```json
{"operations": [
  {"op": "create", "resource": "pages", "ref": "section",
   "fields": {"title": "Bereich", "parent": 3, …}},
  {"op": "create", "resource": "pages",
   "fields": {"parent": {"type": "pages.page", "id": {"ref": "section"}}, …},
   "content": {"main": […]}},
  {"op": "update", "resource": "pages", "id": {"ref": "section"},
   "fields": {"is_active": true}},
  {"op": "update", "resource": "pages", "id": 12, "etag": "…", "fields": {…}},
  {"op": "replace_content", "resource": "pages", "id": 12, "content": {…}},
  {"op": "update_plugin", "resource": "pages", "id": 12,
   "plugin": {"type": "pages.button", "id": 7}, "fields": {…}},
  {"op": "delete", "resource": "articles", "id": 3, "etag": "…"}
]}
```

- Each operation is one of `ContentAPI`'s, named after it, with what its
  route takes: `fields`, `content`, `inlines` as in the body, `etag` as
  in `If-Match`. Each is checked and logged as if done on its own.
- `{"ref": name}` stands for the id of the object created earlier in the
  batch with that `ref`, wherever an id goes: an operation's `id`, a
  reference in `fields` (`parent`), references inside plugins and JSON
  data. A name not created earlier is an error.
- Objects written earlier in the batch need no `etag`: nobody else can
  have changed them since, and the ETag the client read is outdated.
- The operations run in order and stop at the first which fails, since
  later ones may depend on it. Errors carry the operation's index
  (`"operation": 2`) and their paths start with it
  (`operations[2].fields.title`); the status is the operation's.
- The response has each operation's result (`null` for deletions) and the
  ids of the refs, `{"results": […], "refs": {"section": 18}}`. In a dry
  run, what the batch created has no id, as in a single write's dry run.
- Uploads are staged before (`POST /uploads/`) and used as in single
  writes. At most `MAX_BATCH_OPERATIONS` operations (100).

## Pages

- **Creating** needs what the admin form needs: `parent` (a page or `null`
  for a root), `title`, `slug`, `page_type`, `language_code`, and
  `is_active`. feincms3 defaults `is_active` to true, so a client which
  forgot it would publish; the API doesn't pick a default either, the
  client decides. A resource's `required_on_create` lists such fields
  (`PageResource`: `is_active`); they're `required` in the type's schema,
  and a create without them fails with their paths. Excluded and
  read-only fields are left out, so a create stays possible. There's no
  slug from the title: a client writing a title can write a slug. `path`
  is computed by `AbstractPage.save()`, never written, except as
  `static_path` with `path` where the admin allows it.
- **Moving** is a `PATCH` of `parent` and `position` (the position among
  siblings, as in feincms3), through the admin's form like any change, so
  feincms3 updates the descendants' paths and refuses a page below itself.
  No action endpoint of its own.
- **Page types** come from `TYPES`; the schema lists each with its regions.
  Application types are created like any page; `PageTypeMixin`'s clean
  refuses a second instance of an app namespace, as in the admin.
- **Deleting** a page with children is refused (`409`, listing them);
  delete bottom-up, or not at all.
- **feincms3-sites / feincms3-language-sites**: `site` is a field like any
  other where the model has it; `find` takes `host=`, and without it a path
  used on several sites or languages is a `409` listing the pages. The
  scope of a path is read from the model's `unique_together`. Path
  uniqueness is the models' business (feincms3 7.1 and feincms3-sites 0.23
  check new pages too).

## Files

Plugins and objects can have file fields of their own (feincms3's `Image`
plugin has an `ImageField`), or refer to django-cabinet files. Both are
written the same way, in two steps, so every endpoint but one stays JSON:

1. `POST /uploads/` (`multipart/form-data`, just the file) stores the file
   and returns `{"upload": "<id>", "name": …, "size": …}`.
2. The write names it: `{"image": {"upload": "<id>"}}` in a plugin's or
   object's `fields`. The API passes the file to the admin form as an
   uploaded file, so the form's own validation runs (allowed types, image
   checks, django-imagefield's processing) and the field's `upload_to`
   decides where it ends up.

On read, a file field is `{"name", "url"}` or `null`. On write, leaving the
field out keeps the file, `null` clears it where the field is optional.
Writing back what was read (`{"name", "url"}`) keeps it too, so a read,
change, write round trip doesn't need to know about files.

Uploads belong to the user who made them, are used once, and expire when
they aren't used (a day by default; a management command removes them).
They're a staging area: a model of the package (`Upload`: user, file,
created) whose `FileField` stores into the default storage under
`feincms3_api/uploads/` (a setting can name another storage). When a write
uses one, the staged file is opened and handed to the form as an uploaded
file; the target field then saves it like any upload, with its own
`upload_to` and storage, and the staged file and its row are deleted. That's
a copy, not a move: storages have no general move, and `upload_to` may
depend on the instance, which doesn't exist yet at upload time. The other
way, multipart writes carrying the JSON and the files together, would make
every write endpoint deal with multipart.

Uploading from a URL (`{"url": "https://…"}`) is left out: the server
fetching arbitrary URLs is a request forgery risk, and the client can fetch
and upload just as well.

### django-cabinet

Nothing cabinet-specific is needed for files: they go through cabinet's
file admin like any resource, and their `image_file` and `download_file`
fields take uploads. Cabinet's own validation (exactly one file) applies.

- `POST /files/` with `{"fields": {"folder": …, "image_file": {"upload":
  "<id>"}, "image_alt_text": …}}`; `?folder=` filters the list.
- `PATCH /files/{id}/` changes metadata; a new `upload` replaces the file
  under the same id where the model has cabinet's `OverwriteMixin`.
- `DELETE /files/{id}/` of a file still in use returns `409` with the
  referencing objects (cabinet foreign keys are `PROTECT`).
- Listing unused files is left to cabinet: going by foreign keys, the API
  can't tell an unused file from one whose URL is used elsewhere (in rich
  text, a newsletter, another site).
- Folders: a tree of `parent` and `name`; deleting a non-empty folder is
  refused.


## Drafts, revisions, publishing

feincms3 has no drafts or revisions, and the API doesn't add them. What a
site has instead:

- **inactive objects**: create new pages and articles inactive, review in
  the admin, activate (or `PATCH {"is_active": true}`),
- **dry runs** for every change to something live,
- **the ETag** against overwriting an editor's work.

Editing an active page through the API changes the live page. Revisions
are [#5](https://github.com/feincms/feincms3-api/issues/5).

## Authentication and permissions

- `ContentAPI(auth=…)` takes django-ninja auth classes. The default is
  bearer tokens, then ninja's `SessionAuthIsStaff` (for the docs page and
  calls from the admin, with Django's CSRF protection); tokens first, since
  session auth fails without a CSRF token.
- **Bearer tokens** for scripts and agents, from a token model in the
  package (`APIToken`), since the API is tiresome to use without them.
- **Creating tokens:** in the admin, where every active staff user manages
  their own (superusers everyone's) without any model permission, so people
  without server access can get one; the secret is shown once, on a page of
  its own, not in a message (message storage can be a cookie). Or
  `./manage.py f3api_token USERNAME [--write] [--hours N]`, which prints it.
- **Storing them:** a secret is `f3api_` plus 32 random bytes; only its
  HMAC-SHA256 with `SECRET_KEY` (`salted_hmac`) is stored, and tokens are
  found by it. Not a password hasher: those are slow on purpose, against
  guessable secrets, and every request would pay. The HMAC ties tokens to
  `SECRET_KEY`: a leaked database is useless without it, and rotating it
  without a fallback revokes every token at once. Tokens are also found
  under `SECRET_KEY_FALLBACKS`, and moved to the current key when used.
  Lifetimes are short: 8 hours by default (`TOKEN_LIFETIME`, a
  `timedelta`), about one editing session. Logging into the admin (with
  SSO, where a site has it) and creating a token is the start of a session,
  e.g. with an agent; raise the lifetime if that proves tiresome.
- A token belongs to a user and acts with that user's admin permissions,
  never more. Tokens are read-only or read-write, expire, and are stored
  hashed. A read-only token refuses everything but `GET`/`HEAD`/`OPTIONS`.
- Only staff users (`is_staff`) can use the API at all, since every
  permission check goes through the admin.
- Who changed what: `LogEntry` rows as the admin writes them
  (`log_addition`, `log_change`, `log_deletions`), the change message
  followed by "(Through the API.)", so the admin's history shows API
  changes.

## Settings

```python
FEINCMS3_API = {
    "EXCLUDE_FIELDS": [
        "forms.configuredform.recipient_email",
        "pages.jsonplugin_key_figures.data.internal_note",
    ],
    "DEFAULT_PAGE_SIZE": 50,
    "MAX_PAGE_SIZE": 200,
    "MAX_BATCH_OPERATIONS": 100,
    "TOKEN_LIFETIME": timedelta(hours=8),
    "UPLOAD_LIFETIME": timedelta(days=1),
    "UPLOAD_STORAGE": "default",
}
```

`EXCLUDE_FIELDS` entries use the path notation and work for fields and for
values inside JSON data. An excluded field keeps its value on writes; a
required field without a default therefore makes objects of that type
impossible to create through the API. One naming a model or field that doesn't exist is a
system check error (`feincms3_api.E002`), so a typo can't quietly expose a
field. A resource whose model isn't registered with its admin site is one
too (`feincms3_api.E001`), and so is an admin with two inlines for the same
model (`feincms3_api.E003`), because plugins and `inlines` are keyed by
their model's type. The check looks at `ModelAdmin.inlines`, not at what
`get_inlines()` returns for a request.

## Translating

autotranslate (`feincms3_api.incubator.autotranslate`, docs in
`docs/autotranslate.md`) copies content into another language: it dumps
with feincms3-data, translates the strings with a file-based workflow or the
Claude API, and loads the copies as inactive objects with its own loader. It
stays in the incubator as it is for now.

The goal is autotranslate acting through the API: reading the source in
the wire format and creating the copies through the operations, so they get
the admin's validation, permissions and history like any other change
([#7](https://github.com/feincms/feincms3-api/issues/7)). Over HTTP,
translating needs nothing translation-specific beyond the `translation_of`
filter and the kinds: read the source page, translate the strings the
schema marks `text` and `html`, keep `machine`, map links and references
through `translation_of` and `pages/find/`, then create the copy inactive
with `translation_of` set, a dry run first.

**Incubator** means: in the repository and tested, not documented as part
of the package, no stability promise, may be removed.

## Out of scope and rejected alternatives

- **Public, anonymous delivery (headless).** feincms3 sites render
  server-side; a delivery API has other concerns (caching, rendering
  plugins to HTML, published-only). Possible later on the same codecs.
- **Drafts, revisions, workflows.** See "Drafts".
- **Rich text as Markdown** in the stored data: HTML is what's stored and
  what the prose-editor field validates. A project wanting Markdown uses a
  plugin storing it, which the API handles like any other; converting
  between HTML and Markdown on the way in and out is rejected.
- **Copy and translate endpoints** (Wagtail has `copy_for_translation`).
  Translating is built on plain reads and creates, see "Translating".
- **Generic CRUD for any model.** Only registered resources.
- **JSON Patch, GraphQL.** Region replacement and the plugin `PATCH` cover
  the edits; GraphQL buys nothing for writes.

## Credits

Moritz Wettstein started feincms3-autotranslate together with Matthias
Kestenholz in October 2026; its introspection is this package's core.

## Sources

- [Wagtail 8.0 announcement: CMS with AI, not AI CMS](https://wagtail.org/blog/cms-with-ai-not-ai-cms-wagtail-80s-new-api/)
- [Wagtail v3 API reference](https://docs.wagtail.org/en/latest/advanced_topics/api/v3/reference.html)
