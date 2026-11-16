# feincms3-api incubator: autotranslate

Part of the incubator (see the README): no promise of stability.

Copy feincms3 content into another language: dump it with
[feincms3-data](https://github.com/matthiask/feincms3-data), machine-translate
the strings, and load the translated copies as **inactive** objects, which
editors review in the admin before switching them on.

```
 database ──f3dumpdata──▶ source.json ──f3autotranslate──▶ translated.json ──f3autotranslateload──▶ database
                           (dump)     ▲  (strings only)     (not a dump)      (--dry-run first)
                                      │
           f3autotranslateload --plan ┴▶ plan.json (what is missing)
```

```bash
./manage.py f3dumpdata "pages:Angebot|subtree" > pages.json
./manage.py f3dumpdata "articles:*" > articles.json
./manage.py f3autotranslateload pages.json articles.json --target fr --plan > plan.json
./manage.py f3autotranslate pages.json articles.json --target fr --only plan.json -o fr.json --review review.html
./manage.py f3autotranslateload fr.json --dry-run   # the report, nothing kept
./manage.py f3autotranslateload fr.json > load.json  # log on stdout, report on stderr
```

## Setup

```python
INSTALLED_APPS += ["feincms3_data", "feincms3_api.incubator.autotranslate"]
FEINCMS3_DATA_DATASETS = "app.f3datasets.datasets"
FEINCMS3_AUTOTRANSLATE = {
    "BACKEND": "feincms3_api.incubator.autotranslate.backends.anthropic.Anthropic",
    "MODEL": "…",  # required by the anthropic backend, there is no default
    "GLOSSARY": BASE_DIR / "conf" / "translate_glossary.toml",
    "INSTRUCTIONS": "Formal register.",
    "LINK_FIELDS": [],  # beyond URLField, url and *_url
    "EXCLUDE_FIELDS": ["forms.configuredform.recipient_email"],
}
```

```python
# app/f3datasets.py
from feincms3_api.incubator.autotranslate.datasets import (
    language_regions_dataset,
    object_dataset,
    page_tree_dataset,
)


def datasets():
    return {
        "pages": page_tree_dataset(Page, language_regions=[TabList]),
        "articles": object_dataset(Article),
        "tablists": language_regions_dataset(TabList, title_field="technical_title"),
        "trainings": object_dataset(Training, related=[Speaker, Session]),
        "forms": object_dataset(
            ConfiguredForm, offline={"slot": ""}, title_field="name"
        ),
    }
```

`ANTHROPIC_API_KEY` comes from the developer's environment, never from
production settings.

## What can be translated

A **translatable object** is an instance of a model with a `language_code`
whose copies can be kept offline: through `is_active`, or the values its
dataset's `offline={...}` sets on every copy. A model with neither is
refused. The copies get `is_active = False`, the target language, and
`translation_of` pointing at the original where the model has it.

- **Plugins** (django-content-editor) are found automatically and copied
  along.
- **Other dependent models** have to be listed: `related=[Speaker, Session]`,
  or `(Model, "field_name")` where the foreign key is ambiguous. Discovering
  "everything pointing at this model" would dump visitor data along with the
  content.
- **Pages** are walked as trees. Roots are never copied, they map to the
  target language's root. A page's parent needs a counterpart in the target
  language, otherwise the load stops and names the page to translate first
  (or pass `--parent <pk>`).
- **Objects with a region per language** (a tab list rendering
  `{% render_region tabs LANGUAGE_CODE %}`) aren't copied: their source
  region's plugins are translated into the target region of the same object.
  Name them with `language_regions=[...]` so they come along with the pages
  using them, or select them with `language_regions_dataset()`. **There is no
  `is_active` for a region**: if active target-language content already uses
  the object, the new region is live as soon as it's loaded. The report lists
  these first.

### Loading active copies

`f3autotranslateload --activate` creates the copies with `is_active = True`,
for a language which goes live without a review in the admin (a new site
section, or a language nobody reads yet). The report says so at the top of
its warnings. It changes nothing else:

- translations which exist already keep their `is_active`, also with
  `--replace`, since activating them is an editor's decision;
- `offline={...}` of a dataset still applies, so a model without
  `is_active` stays offline the way its dataset says.

Pass it to the dry run too, so the report describes the load which follows.

## Selection

`Angebot`, `angebot` or `17` (exact title, exact slug, pk), with pages
`Angebot|subtree`, and `*` for everything in a language, plus `|lang=<code>`
(default: the first language) and `|site=<host>` for feincms3-sites.

## Two gates

1. **Datasets decide which rows are dumped.** Only datasets registered in
   `FEINCMS3_DATA_DATASETS` can be dumped, and the loader refuses dumps
   containing models no autotranslate dataset registers.
2. **The translatable fields decide which strings are sent.**
   `./manage.py f3autotranslate --list-fields` prints them per model; commit
   its output as a snapshot test, so a new field is sent only after someone
   has accepted the diff.

The default is editable `CharField`s and `TextField`s without `choices`,
except slugs, emails, URLs, link fields and a few names (`slug`, `path`,
`meta_robots`, …). django-prose-editor fields are sent as HTML.
django-json-schema-editor data contributes every string property without
`enum` or `pattern` (any pattern: it constrains the value to something
machine-readable), and without a `format` other than `textarea` or `prose`.
feincms3-forms fields keep their `name` and `default_value`, and only the
labels of their `choices` are translated, so stored submissions keep meaning
the same.

`EXCLUDE_FIELDS` and `LINK_FIELDS` name `app_label.model_name.field`, plus a
path into JSON data (`pages.jsonplugin_key_figures.data.figures[].unit`); an
entry naming nothing is a system check error.

## References

Foreign keys, many-to-many fields, django-json-schema-editor
`foreign_key_paths`, first-party `href`s in HTML and link fields (`URLField`,
`url`, `*_url`, `LINK_FIELDS`) all follow one rule: a reference to an object
of the run goes to its copy; otherwise to its existing translation (through
`translation_of`); otherwise it stays, and the report lists it if the target
is in another language. The report also lists referenced objects with a
region per language without plugins in the target region, and referenced
django-translated-fields objects whose target-language column is empty.

## Running again

Running the same selection again is safe: an object with a translation is
skipped, and only what's missing is created, attached under the existing
translations. The plan makes sure only that is translated and paid for.

`--replace` replaces the content of existing translations (their translated
fields, plugins and related objects) and keeps what an editor decided about
them: pk, slug, path, parent, position, menu, `is_active`. Corrections made
in the replaced content are lost.

Models without `translation_of` can't recognise an earlier copy: running
twice creates a second one. A slug clash then stops the object instead of
getting a `-2` suffix. Delete the old copy first, or name it with
`--into app.model:<original pk>=<copy pk>`, or `--into load.json`.

## Translators

- **files** (built in): `--prepare` writes `strings.json`, which a person or
  an agent translates (same keys, tags unchanged), `--apply
  strings.fr.json -o fr.json` reads it back. With `--max-chars N`,
  `--prepare` writes `strings-1.json`, `strings-2.json`, … of at most N
  characters each, every one with the rules, to be translated in parallel;
  `--apply` takes them all.
- **anthropic** (`feincms3-api[anthropic]`): the Claude API, one
  request per object (split by size when needed). A cut-off, refused or
  invalid answer fails the object; the next run translates it again. Answers
  are written to `<output>.partial` as they arrive, and `--resume` continues
  an interrupted run.

## Sharp edges

- **Signals run in the dry run.** A rolled-back transaction undoes database
  writes, not what `post_save` handlers do outside of it (mail, API calls,
  files). Handlers using `transaction.on_commit()` are fine.
- **Static paths aren't translated.** Copies get `static_path = False`, and
  the report names the original path.
- **A translated file fits only the database it was dumped from.** It refers
  to that database's primary keys.

## Tests

```bash
cd tests && python manage.py test testapp
tox                    # every supported Python and Django version
```
