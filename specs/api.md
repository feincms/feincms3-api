# feincms3-api: spec (draft)

A django-ninja API for reading and writing the content of feincms3 sites:
pages and other objects with django-content-editor regions and plugins,
django-json-schema-editor data and django-cabinet files. For scripts,
importers and agents (e.g. Claude Code with a token and `curl`), next to the
admin, not instead of it.

It grows out of feincms3-autotranslate (see "Credits"): its content
introspection becomes this package's core, and its translation pipeline
lives in an incubator subpackage (see "Translating" and "Layout"). Ideas
taken from it: explicit allowlists, a committed snapshot of what's exposed,
dry runs in a rolled-back transaction, errors as report lines instead of
tracebacks.

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

- **Writes go through the admin's forms.** `ModelAdmin.get_form(request,
  obj)` for the object, the inline's form for each plugin. The wire value is
  turned into form data, `form.is_valid()` runs exactly the validation the
  admin runs, `form.save()` saves. A plugin with custom validation behaves
  the same through the API and in the admin, without the project writing
  anything for the API.
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
a browser (messages, redirects). Only `get_form`, `get_formset`/inline forms,
`get_queryset`, `get_fields`/`get_readonly_fields` and the permission methods
are called; anything else in a `ModelAdmin` is never touched.

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
| `ObjectResource` | any model, with or without plugins | nothing; articles, blog entries, events, tab lists, feincms3-forms forms |
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
- **References** are `{"type", "id"}` objects on read; on write a bare id is
  accepted where the field has one target model. `?expand=references` adds
  `title` and `url` (pages) or `url`, `name`, `width`, `height`, `alt_text`
  (cabinet files), so a reader doesn't need one request per reference.
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
| `CharField`, `TextField` widget | string | `maxLength` |
| `ChoiceField`, `TypedChoiceField` | string | `enum` |
| `BooleanField`, `IntegerField`, `DecimalField`, date/time fields | as JSON | type, bounds, format |
| `URLField` | string | `format: uri` |
| `ModelChoiceField`, `ModelMultipleChoiceField` | reference(s) | target type, nullable |
| cabinet's `CabinetForeignKey` form field | file reference | target `cabinet.file`, images only if `limit_choices_to` says so |
| django-prose-editor field | HTML string | `contentMediaType: text/html`, allowed tags from the field's extensions |
| django-json-schema-editor `JSONEditorField` | the JSON value | the field's own schema, `foreign_key_paths` as references |
| django-imagefield `PPOIField` | `"0.5x0.5"` | `pattern` |
| `FileField`, `ImageField` (the object's own file) | `{"name", "url"}`; written as `{"upload": id}` | `x-feincms3-file` |

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

**Adapters** decide a plugin's (or object's) `fields` as a whole. The default
adapter is the admin form with the codecs, and covers every plugin made of
ordinary fields without any code, e.g. a button plugin with `button_label`,
`button_url` and `button_page` fields. Adapters shipped:

- **default** (the form's fields through codecs),
- **feincms3 plugins**: `RichText`, `HTML`, `External`, `Snippet` (template
  choices from the model), `Image` (its own file, see "Files"),
- **json-schema-editor proxies**: `fields.data` with the proxy's `SCHEMA`;
  the form field validates it, references come from `foreign_key_paths`,
- **feincms3 mixins on objects**: `page_type`/`template_key` with their
  choices, `menu`, `language_code`, `translation_of`, `redirect_to_page` and
  `redirect_to_url`, feincms3-meta's fields.

A project writes an adapter only to change the shape, e.g. to group
`button_label`, `button_url`, `button_page` into one `button` object:

```python
from feincms3_api.adapters import FormAdapter, Group

content_api.adapters.register(
    "pages.button",
    FormAdapter(groups=[Group("button", prefix="button_")]),
)
```

An adapter has three methods, `to_wire(obj)`, `to_form_data(value,
instance)` and `json_schema()`; anything shaped very differently (a plugin
storing a list in a `TextField`) subclasses it.

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
| `GET /{resource}/` | list: `limit`, `offset`, `q` (the admin's `search_fields`), filters declared on the resource (default: `language_code`, `is_active`, `translation_of`, `site`; pages also `parent`, `page_type`, `path`), `fields=` to thin out |
| `POST /{resource}/` | create, with or without `content` |
| `GET /{resource}/{id}/` | one object with its content |
| `PATCH /{resource}/{id}/` | change fields; `content` if given, see below |
| `PUT /{resource}/{id}/content/` | replace the content of the regions given |
| `GET /{resource}/{id}/plugins/{type}/{plugin_id}/` | one plugin |
| `PATCH /{resource}/{id}/plugins/{type}/{plugin_id}/` | change one plugin's fields |
| `DELETE /{resource}/{id}/` | delete |
| `GET /pages/find/?path=/de/angebot/` | resolve a path, also with host for multisite |
| `POST /uploads/` | upload a file (multipart), returns an upload id, see "Files" |
| `GET|POST /files/`, `GET|PATCH|DELETE /files/{id}/` | cabinet, see below |
| `GET|POST /folders/`, `GET|PATCH|DELETE /folders/{id}/` | cabinet folders |

Lists return `{"count", "items"}`, items without `content` unless
`?content=true`.

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
region.

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
fields and content in wire format. Every `PATCH`, `PUT` and `DELETE` needs
`If-Match` (`428` without, `412` if the object changed since), the plugin
`PATCH` included, with the object's ETag. The admin doesn't check anything,
so an admin save can still overwrite an API write; the other direction is
covered.

## The service layer

The endpoints are thin: under them is a Python service layer of functions
taking a user and wire data (`read(user, resource, pk)`, `create(user,
resource, data, *, dry_run)`, `update(…)`, `replace_content(…)`), which do
the form handling, permission checks, validation and logging. The ninja
endpoints only parse the request, call one function and map its result or
errors to a response.

Two reasons: the logic is testable without HTTP, and work that spans many
objects can run in one transaction in process. A subtree is many creates,
and its dry run and its load should each be one `atomic()`; over HTTP that's
many requests. Management commands (autotranslate, importers) call the
service layer directly; remote scripts and agents use HTTP and go object by
object. A batch endpoint (`POST /batch/`, several operations in one
transaction) would close that gap over HTTP; later, if it's needed.

## Pages

- **Creating** needs `parent` (a page or `null` for a root), `title`,
  `page_type`/`template_key`, `language_code`; `slug` defaults to
  `slugify(title)`. `path` is computed by `AbstractPage.save()`, never
  written, except as `static_path` with `path` where the admin allows it.
- **Moving** is a `PATCH` of `parent` and `position` (the position among
  siblings, as in feincms3), through the admin's form like any change, so
  feincms3 updates the descendants' paths and refuses a page below itself.
  No action endpoint of its own.
- **Page types** come from `TYPES`; the schema lists each with its regions.
  Application types are created like any page; `PageTypeMixin`'s clean
  refuses a second instance of an app namespace, as in the admin.
- **Deleting** a page with children is refused (`409`, listing them);
  delete bottom-up, or not at all.
- **New pages are inactive** unless `is_active` is given. Pages edited
  through the API stay as they are, see "Drafts".
- **feincms3-sites / feincms3-language-sites**: `site` is a field like any
  other where the model has it; `find` takes `host=`. Path uniqueness is the
  models' business. Tested in step 4, not before.

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
- `GET /files/?unused=true` lists files no registered model refers to, the
  report people ask for anyway. Not built yet.
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
(django-reversion, or a JSON snapshot of the wire format before each write)
are a possible later addition: the wire format would make a good revision
format. Not v1.

## Authentication and permissions

- `ContentAPI(auth=…)` takes django-ninja auth classes; the default is
  ninja's `SessionAuthIsStaff`, which is enough for the docs page and calls
  from the admin.
- **Bearer tokens** for scripts and agents, from a token model in the
  package (`APIToken`), since the API is tiresome to use without them. The
  default auth is tokens, then sessions: tokens first, since session auth
  fails without a CSRF token.
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
  `timedelta`), about one editing session. Logging into the admin (with SSO, where a site has
  it) and creating a token is the start of a session, e.g. with an agent;
  raise the lifetime if that proves tiresome.
- A token belongs to a user and acts with that user's admin permissions,
  never more. Tokens are read-only or read-write, expire, and are stored
  hashed. A read-only token refuses everything but `GET`/`HEAD`/`OPTIONS`.
- Only staff users (`is_staff`) can use the API at all, since every
  permission check goes through the admin.
- Who changed what: `LogEntry` rows as the admin writes them
  (`log_addition`, `log_change`, `log_deletion`), with "via API" in the
  change message, so the admin's history shows API changes.

## Settings

```python
FEINCMS3_API = {
    "EXCLUDE_FIELDS": [
        "forms.configuredform.recipient_email",
        "pages.jsonplugin_key_figures.data.internal_note",
    ],
    "DEFAULT_PAGE_SIZE": 50,
    "MAX_PAGE_SIZE": 200,
}
```

`EXCLUDE_FIELDS` entries use the path notation and work for fields and for
values inside JSON data. One naming a model or field that doesn't exist is a
system check error (`feincms3_api.E002`), so a typo can't quietly expose a
field. A resource whose model isn't registered with its admin site is one
too (`feincms3_api.E001`).

## Translating

autotranslate (`feincms3_api.incubator.autotranslate`, docs in
`docs/autotranslate.md`) copies content into another language: it dumps
with feincms3-data, translates the strings with a file-based workflow or the
Claude API, and loads the copies as inactive objects with its own loader. It
stays in the incubator as it is for now.

**The goal: autotranslate acts through the API.** Reading the source in the
API's wire format instead of a feincms3-data dump, and creating the copies
through the service layer instead of its own loader, so translated copies
get the admin's validation, permissions and `LogEntry` like any other
change. What stays autotranslate's own: choosing the strings (from the
kinds), calling the translator, the glossary, the review file, mapping
references to their counterparts. What goes: the dump format,
`translated.json`, most of `loader.py` (save order, `full_clean()`, the
per-model rewriting).

Over HTTP, translating is smaller still, e.g. as an agent skill: read the
source page, translate the strings the schema marks `text` and `html`, keep
`machine`, map `link`s and references through `translation_of` and
`pages/find/`, then `POST` the copy inactive with `translation_of` set,
`dry_run=true` first. Page by page, reviewed in the admin. The API needs
nothing translation-specific for it beyond the `translation_of` filter and
the kinds.

## Layout

- `feincms3_api/`: the package. The introspection moves here from
  autotranslate step by step: `fields.py` (kinds, schema walking, path
  notation, `iter_path`), `datasets.specs_for_plugins` (plugin discovery),
  `links.py` (first-party URLs), the path scope of the three page shapes.
- `feincms3_api/incubator/autotranslate/`: the rest of autotranslate, its
  own app (label `feincms3_autotranslate`), the `[anthropic]` extra, the
  `FEINCMS3_AUTOTRANSLATE` setting.
- `specs/`: this file.

**Incubator** means: in the repository and tested, not documented as part
of the package, no stability promise, may be removed. Its tests stay, since
its test app is a good base for the API's own. It either graduates (rebuilt
on the service layer) or goes away in favour of a skill.

Nothing imports from a project; the test app has feincms3 pages, JSON
plugins and cabinet.

## Order of work

0. The repository: autotranslate moved into the incubator, setup, credits.
   Done.
1. Read side: resources, codecs on top of the core, default and JSON
   adapters, `/schema/` with the kinds, `GET` list and detail, the
   snapshot command. Done, with session auth. Left for later: tokens,
   references inside JSON data (`foreign_key_paths`, now `machine`
   strings), JSON paths in the `EXCLUDE_FIELDS` check, trying it against a
   real site's content.
2. Writes, the service layer first and endpoints around it: object
   `PATCH`/`POST`, content `PUT`, plugin `PATCH`, dry run, ETags,
   `LogEntry`, error paths. Done. Fields go into the admin's forms through
   a widget which hands values to the fields' `clean()` as they are (the
   admin's widgets expect HTML form data). Read-only fields may be written
   back unchanged; values hidden by `EXCLUDE_FIELDS` inside JSON data are
   kept by their position. Change messages are the admin's, followed by
   "(Through the API.)".
3. Pages (tree, move, find, translations filter), uploads and file fields,
   cabinet (files, folders, unused files). All done but unused files; moving
   needed nothing beyond the admin's form, the translations filter is the
   default `translation_of` filter.
4. feincms3-sites and feincms3-language-sites test apps. Done: `find/`
   with `host=`, the scope read from `unique_together`. Found on the way:
   neither feincms3 nor Django's unique check refuses a new page whose
   path is taken (feincms3 checks existing pages only, Django skips
   `("site", "path")` since `site` isn't a form field), so the admin
   answers with a 500. Fixed upstream in feincms3 7.1 and feincms3-sites
   0.23, which the API requires.
5. First alpha; a second project; autotranslate on the service layer
   (graduating from the incubator), a translating skill over HTTP.
6. Later, maybe: typed OpenAPI, i.e. components generated from the types'
   JSON schemas at startup (as a superuser sees them), with plugins as a
   union on `type`; until then OpenAPI has `fields` and `content` as plain
   objects, and `/schema/{type}/` has the real shapes. A CLI or MCP server
   on top (as Wagtail did with `wagtail-cli`, `wagtail-mcp`); Markdown for
   rich text; revisions.

## Out of scope and rejected alternatives

- **Public, anonymous delivery (headless).** feincms3 sites render
  server-side; a delivery API has other concerns (caching, rendering
  plugins to HTML, published-only). Possible later on the same codecs.
- **Drafts, revisions, workflows.** See "Drafts".
- **Rich text as Markdown.** HTML is what's stored and what the
  prose-editor field validates. Markdown in and out, as Wagtail offers, can
  come later as `?rich_text_format=markdown`, converted on the server.
- **Copy and translate endpoints** (Wagtail has `copy_for_translation`).
  Translating is built on plain reads and creates, see "Translating".
- **Generic CRUD for any model.** Only registered resources.
- **JSON Patch, GraphQL.** Region replacement and the plugin `PATCH` cover
  the edits; GraphQL buys nothing for writes.

## Decided

- **The name** is feincms3-api.
- **A token model ships with the package.**
- **Content is flat**, with `depth`; nested only as a read option.
- **List filters are declared on the resource** (`filters=`), not taken
  from the admin's `list_filter`: its entries can be arbitrary
  `SimpleListFilter`s, and the admin's use case (narrowing a list for a
  person) isn't the API's.
- **First uses:** both an importer for a relaunch and a site that's already
  built.

## Credits

Moritz Wettstein started feincms3-autotranslate together with Matthias
Kestenholz in October 2026; its introspection is this package's core.

## Sources

- [Wagtail 8.0 announcement: CMS with AI, not AI CMS](https://wagtail.org/blog/cms-with-ai-not-ai-cms-wagtail-80s-new-api/)
- [Wagtail v3 API reference](https://docs.wagtail.org/en/latest/advanced_topics/api/v3/reference.html)
