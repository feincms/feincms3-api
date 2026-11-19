# Change log

## Next version

- Tests run with the oldest and the newest Python per Django version.
- Fixed: writes with an invalid token (expired, revoked) were a `403`
  "CSRF check Failed" of the session auth tried next instead of a `401`.
  Requests with a bearer token don't fall back to the session anymore.

## 0.3 (2026-11-17)

- The "token created" page uses the admin's own breadcrumbs, which Django
  6.1 renders with new markup.
- Paths below the API's prefix which match nothing are a JSON `404`
  naming the resources, not the site's HTML 404 page.
- Fixed: dry runs, and writes or batches which failed, left the copies of
  their uploads in the target fields' storage.
- Fixed: the staged file of an upload stayed open after the write using
  it (a `ResourceWarning`).

## 0.2 (2026-11-17)

- The token admin's user defaults to the current user.
- `?q=` on a resource whose admin has no `search_fields` is a `400`
  instead of returning everything; `/schema/` lists each resource's
  `filters` and whether it can be searched (`search`).
- The token admin shows the access as radio buttons and the user as a raw
  id field; the token's name is optional.
- Kinds: `link` and `*_link` are link names, feincms3's HTML plugin's `html`
  is `machine`, and the new `KINDS` setting sets the kind per field or JSON
  path where the type and name don't tell (system check
  `feincms3_api.E004`).
- Batches: an `etag` given for an object written earlier in the batch is
  ignored instead of failing with 412; a field called `ref` isn't taken for
  a reference; operations of the wrong shape (an `op`, `ref` or `resource`
  which isn't a string) are a 422, not a 500.
- The OpenAPI schema offers `replace_content` and `update_plugin` batch
  operations only for resources with plugins.
- Added an integration guide, `docs/integration.md`.

## 0.1 (2026-11-17)

Backwards incompatible:

- Removed the incubator's autotranslate (and the `autotranslate` and
  `anthropic` extras): translating is done over the API, see
  `docs/translating.md`. Remove `feincms3_api.incubator.autotranslate`
  from `INSTALLED_APPS`.
- Creating a page needs `is_active`: feincms3 defaults to active, so a
  client has to decide. Resources list such fields in `required_on_create`
  (also an argument), which the type's schema marks as `required`.
- The plugin `PATCH` returns the object's `meta.warnings` and `etag` in the
  plugin's `meta`; `ContentAPI.update_plugin()` returns only the plugin.

Other changes:

- Added `feincms3-api-cli` (`cli/`, command `f3api`), a separate package:
  the API from the command line, with dry runs unless `--apply`, the ETags
  of what was read sent automatically, and errors with their paths.
- The schema of a django-prose-editor field with `extensions` says which
  markup it allows (`x-feincms3-html`: tags, attributes, URL schemes) and
  whether the field sanitises the rest away.
- Added `POST /batch/` (`ContentAPI.batch()`): several writes in one
  transaction, all or none, with one dry run for all of them;
  `{"ref": name}` stands for the id of an object created earlier in the
  batch. `MAX_BATCH_OPERATIONS` limits its size. The write operations take
  `created=`, a list collecting what they create.
- The OpenAPI schema is typed: every route's body and response refers to
  components built from the types' JSON schemas (a superset, as a
  superuser sees them), with plugins as a `oneOf` by `type` per region, so
  generated clients get real types. They are built when the schema is
  first requested, not at startup.
- Fewer queries: hidden plugins are counted in one query instead of read
  per plugin table, and lists with `content=true` fetch the plugins once
  per inline for all items. `Resource.plugins()` returns only the visible
  plugins; `count_hidden_plugins()` and `plugins_of()` are new.
- Added `DELETE /{resource}/{id}/cache/` (`ContentAPI.clear_cache()`) to
  delete an object's cached rendering, so a client can check its changes on
  the site at once; `Resource.cache_keys()` says which keys.
- A system check (`feincms3_api.E003`) reports a registered model's admin
  with two inlines for the same model, which the API can't tell apart.
- Documented the setup in the README, and the code's structure in
  `docs/architecture.md`.

## 0.0.1 (2026-11-16)

- Added `?expand=references`: `meta.references` describes everything an
  object refers to (`Resource.describe()`), django-json-schema-editor's
  references inside JSON data included, which the schema now marks.
- Writes go through the admin's formsets and save hooks: every inline is
  bound to the formset the admin would build, and saving runs
  `save_form()`, `save_model()`, `save_related()` (so `save_formset()`) and
  `construct_change_message()`, also for one plugin's `PATCH`.
- Added `inlines`: the admin's inlines which aren't plugins, read and
  written by type, each type given replaced like a region.
- Added `Resource.check()` for problems which don't stop saving, returned
  in `meta.warnings`, now `{"level", "message", "path"}` objects, and
  `feincms3_api.forms.ConfiguredFormResource`, which reports the
  feincms3-forms form type's validation there.
- Added `TreeResource` for django-tree-queries trees which aren't pages
  (chapters, categories); `PageResource` builds on it.
- Plugins are read through the inlines' querysets, so feincms3-forms' field
  proxies each show their own fields; form types are region sets.
- Added feincms3-sites and feincms3-language-sites support: `find/` takes
  `host=` and looks up the page in the host's site or language; the scope
  comes from the page model's `unique_together`. An `IntegrityError`
  during a write is a `409`, not a 500.
- Raised the minimum feincms3 version to 7.1, which refuses new pages whose
  path is taken.
- Added django-cabinet support: `FileResource` and `FolderResource` in
  `feincms3_api.cabinet`. Strings named `ppoi` or `*_ppoi` (django-imagefield)
  are machine strings.
- Added `pages/find/?path=…`. Pages are moved by changing `parent` and
  `position`.
- Added staged uploads: `POST /uploads/` stores a file for a day
  (`UPLOAD_LIFETIME`), a write names it as `{"upload": id}` in a file field,
  which saves a copy with its own `upload_to`, and the upload is removed
  once the write is committed. `./manage.py f3api_cleanup` removes expired
  uploads and tokens.
- Added the write side: `POST` to create objects with their content,
  `PATCH` for fields and regions, `PUT …/content/` for regions, `PATCH` for
  one plugin, `DELETE`. Writes go through the admin's forms, need
  `If-Match` with the ETag, fail as a whole with every problem and its
  path, can be dry runs (`dry_run=true`), and are logged in the admin's
  history.
- Added API tokens (`APIToken`): bearer authentication before session
  authentication, read-only or read-write, expiring after 8 hours by
  default (`TOKEN_LIFETIME`, a `timedelta`). Staff users create their own in
  the admin, which shows the secret once, or with `./manage.py f3api_token`.
- Added the read side of the content API: `ContentAPI` with `PageResource`
  and `ObjectResource`, forms, permissions and plugins taken from the admin,
  `GET` lists and details with the plugins flat with their depth (or
  nested with `nested=true`), list filters declared on the resource,
  `/schema/` with the
  kind of every string, and the `f3api_schema` snapshot command.
- Started feincms3-api from feincms3-autotranslate. The translation pipeline
  lives on in `feincms3_api.incubator.autotranslate`, its app label stays
  `feincms3_autotranslate`.
- feincms3-autotranslate: dump feincms3 content with feincms3-data, translate
  it through a file-based workflow or the Claude API, and load the copies as
  inactive objects.
