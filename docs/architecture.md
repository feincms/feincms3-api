# How feincms3-api is built

An overview of the code: what happens to a request, which module does what,
and where a project plugs in its own code. The reasoning behind the design
is in [the spec](../specs/api.md).

## The idea in one paragraph

The API doesn't describe the content a second time. Everything comes from
the Django admin: which objects a user can find (`get_queryset`), what they
may do (`has_*_permission`), which fields they see and how those are
validated (`get_form`, the inlines' formsets), which plugins go into which
region (the `ContentEditorInline`s), and how saving works (`save_model`,
`save_related`, `save_formset`). The API translates between JSON and those
forms, and adds what an HTTP client needs on top: a schema, paths for
errors, ETags, dry runs, tokens and uploads.

## A request, from top to bottom

```
HTTP ──▶ http.py ──▶ ContentAPI (api.py)
          ninja       ├─ reading.py   list, detail, find, plugin
          routes      ├─ writing.py   create, update, update_plugin, delete, clear_cache, upload
                      ├─ batch.py     several writes in one transaction
                      ├─ schema.py    /schema/, /schema/{type}/
                      └─ openapi.py   typed OpenAPI components
                              │
                              ▼
                      Resource (resources.py)        what the admin says
                              │
             ┌────────────────┼──────────────────┐
             ▼                ▼                  ▼
        adapters.py      content.py         references.py
        an object's or   planning a write   ?expand=references
        plugin's fields  (every inline row)
             │                ▼
             ▼           formsets.py
        codecs.py        the rows bound to the admin's formsets
        one form field
```

- **`http.py`** builds the django-ninja router: one set of routes per
  registered resource. The routes only parse the request, call one method
  of `ContentAPI` and turn the result or an `APIError` into a response. No
  logic lives here.
- **`api.py`**: `ContentAPI` holds the registered resources, the codec and
  adapter registries and the auth classes, and is put together from four
  mixins with the operations. The operations are plain methods taking a
  request and returning wire data, so management commands and other code
  can call them without HTTP, several of them in one transaction.
- **`reading.py`** reads: lists with filters and pagination, one object,
  one plugin, an object by its path.
- **`writing.py`** writes, as the admin's change view does: bind the
  object's form, plan the inline rows, bind the formsets, validate
  everything and collect every error with its path, then `save_form()`,
  `save_model()`, `save_related()`, the change message and the history
  entry. One transaction per write; a dry run rolls it back.
- **`openapi.py`** builds the OpenAPI schema's components from the types'
  JSON schemas, as a superuser sees them, when the OpenAPI schema is first
  requested. The routes annotate their bodies and responses with them;
  the annotations validate a body only as an object (or as before, with the
  route's pydantic model), so validation stays with the operations.
- **`batch.py`** runs a batch's operations in order through the other
  operations, in one transaction, replacing `{"ref": name}` with the ids of
  what the batch created; errors get the operation's index.
- **`schema.py`** describes what's there: the resources, their region sets
  (page types, templates, form types), the plugins allowed per region, the
  other inlines, and each type's JSON schema.

## The pieces

**Resources** (`resources.py`, `cabinet.py`, `forms.py`). A resource is a
model registered with the API. It reads everything from the model's admin:
queryset, permissions, form, inlines, regions. Subclasses add what a kind
of model needs: `TreeResource` (a `parent` filter, no deleting nodes with
children), `PageResource` (paths, `find/`, feincms3-sites and
feincms3-language-sites), `FileResource` and `FolderResource`
(django-cabinet), `ConfiguredFormResource` (feincms3-forms validation as
warnings). Hooks for projects: `describe()` (what a reference to an object
shows), `check()` (problems which don't stop saving, in `meta.warnings`),
`validate_instance()`, `check_delete()`, `cache_keys()` (what
`DELETE …/cache/` deletes), `find()`, `filters=`.

**Adapters** (`adapters.py`) decide what an object's or a plugin's `fields`
are. The default `FormAdapter` takes the admin form's fields and runs each
through its codec: `to_wire()` for reading, `form_data()` for writing (the
current values with the wire values decoded over them), `json_schema()` for
the schema. Projects register an adapter per type only to change the shape.
Codecs decide one field; an adapter decides a type's `fields` as a whole,
which is where a project goes when the admin form is a poor wire shape:
fields grouped into one object, a list or JSON stored in a `TextField`,
fields a form builds in `__init__` from other data, or values set in
`save_model()` that shouldn't be written directly. So far the default
adapter covers every type; the registry is the seam for when it doesn't.

**Codecs** (`codecs.py`) handle one form field: its wire value, how a wire
value becomes form data, its JSON schema, and the references in it. They're
looked up by form field class along the MRO. A form field without a codec
isn't guessed at; it's left out and listed as excluded.

**`RawWidget`** (in `adapters.py`) is the trick that makes the admin's forms
usable for JSON: the admin's widgets read HTML form data (a date and a time
as two inputs, a checkbox as present or not), while the API's values are
already what the fields clean. Every bound form gets this widget, which
hands the value to the field's `clean()` as it is.

**Planning and formsets** (`content.py`, `formsets.py`). A write starts
with every row of every inline unchanged, plugins and other inlines alike.
The `Planner` applies the request: replaced regions (with their regions,
allowed types, depths, ids and permissions checked), replaced `inlines`, or
one plugin's fields. Each row ends up unchanged, changed, new or deleted.
`bind_formset()` turns the rows of an inline into the formset the admin
would build, with a form for every row, so the formset's own validation sees
all of them and `save_formset()` gets what it gets in the admin.

**Kinds and paths** (`kinds.py`, `paths.py`, from feincms3-autotranslate).
Every string in the schema says what it is (`text`, `html`, `link`,
`machine`, `choices`), which is what translating and spell checking need.
Paths (`content.main[2].fields.data.figures[0].title`) locate a value
inside an object; errors, exclusions and the snapshot use them.

**References** (`references.py`). `?expand=references` collects the
references in an object's wire data through the codecs (JSON data
included) and describes them with `Resource.describe()`, in
`meta.references`, without touching the references themselves.

**Auth, tokens and uploads** (`auth.py`, `models.py`, `admin.py`).
`APIToken` stores an HMAC of the secret, is found by it, and acts as its
user; staff users manage their own tokens in the admin, and list and
revoke them over the API (`tokens.py`, `GET /tokens/`, `DELETE
/tokens/{id}/`, `DELETE /tokens/current/`). `Upload` stages a
file for a later write; the target field saves a copy with its own
`upload_to` once the write uses it. `stored.py` deletes those copies again
when the write or its batch is rolled back (a dry run, an error), since
rolling back the transaction doesn't remove files.

**Settings and checks** (`conf.py`, `apps.py`): `FEINCMS3_API`, and system
checks for resources without an admin and for `EXCLUDE_FIELDS` and
`KINDS` entries which don't exist.

**Management commands**: `f3api_schema` (the snapshot: every type with its
fields and their kinds, for a snapshot test), `f3api_token`,
`f3api_cleanup` (expired uploads and tokens).

**The CLI** (`cli/`, its own package): `f3api` (also installed as `feincms3-api-cli`, for `uvx`) talks HTTP with a token
(click for the commands, rich for tables and errors). Writes are dry runs unless `--apply`; it remembers
the ETag of each object it read (`~/.cache/f3api/etags.json`) and sends
that one, so a write fails rather than overwrite a change made since.
Claude Code skills using it live in `cli/feincms3_api_cli/skills/<name>/`;
`f3api skills install` links them into Claude Code's skills.

## Where a project plugs in

| To | Use |
|---|---|
| expose a model | `content_api.register(ObjectResource(Model))`, or one of the subclasses |
| filter lists | `ObjectResource(Model, filters=[...])` or `filter_fields` on a subclass |
| make a create give a field the model has a default for | `ObjectResource(Model, required_on_create=[...])` or `required_on_create` on a subclass |
| hide a field, or a value inside JSON data | `FEINCMS3_API["EXCLUDE_FIELDS"]` |
| say what a string is where its type and name don't | `FEINCMS3_API["KINDS"]` |
| support a form field the API doesn't know | `content_api.codecs.register(FieldClass, Codec())` |
| change a type's shape on the wire | `content_api.adapters.register("app.model", Adapter())` |
| describe references to a model differently | `Resource.describe()` |
| report problems which don't stop saving | `Resource.check()` |
| refuse a deletion | `Resource.check_delete()` |
| anything about validation, permissions, saving | the admin, as always |

## Tests

`tests/testapp` is a feincms3 site with pages, articles, JSON plugins,
cabinet, feincms3-forms, a tree without paths and a model with ordinary
inlines; `tests/sitesapp` and `tests/langapp` are feincms3-sites and
feincms3-language-sites. `tests/testapp/snapshot.txt` is the schema
snapshot, regenerated with `f3api_schema` and reviewed as a diff.
