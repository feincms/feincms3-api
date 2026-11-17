# Translating content over the API

Translating a page (or a section, or an article) into another language
needs no translation endpoint: it's reads, the schema's kinds, and one batch
of creates. A script or an agent with the [CLI](../cli/README.md) (or
`curl`) does it like this; the examples use `f3api`, German to French.

With Claude Code, the CLI's `feincms3-translate` skill does these steps
(`f3api skills install`, see the [CLI's README](../cli/README.md#skills)):
selecting a subtree, skipping what's translated already, a glossary,
objects with a region per language, and a report for the editors.

## 1. Read the source and the schema

```bash
f3api get pages 17 --expand > source.json        # fields, content, meta.references
f3api schema pages.page                           # the page's fields
f3api schema pages.richtext                       # each plugin type used
```

Every string in a type's schema has an `x-feincms3-kind`:

| kind | what to do |
|---|---|
| `text` | translate |
| `html` | translate the text, keep the markup; `x-feincms3-html` says which markup the field allows |
| `link` | map to the counterpart in the target language (below), or keep external URLs |
| `machine` | keep (slugs are the exception: give the copy its own, from the translated title) |
| `choices` | `value\|label` lines: translate the labels, keep the values |

`readOnly` fields (`path`, …) are left out of the copy.

## 2. Map links and references

References (`{"type", "id"}`, and the bare ids JSON data marks with
`x-feincms3-ref`) and internal links point to objects in the source
language. Find each one's counterpart:

```bash
f3api list pages -f translation_of=44 -f language_code=fr --fields title,path
f3api find /de/kontakt/                           # a link's page, then as above
```

- A counterpart exists: use its id, or its path for links.
- None exists yet: translate that object first (in the same batch, with a
  `ref`), or keep the reference and note it for the editors.
- Files (`cabinet.file`) and other objects without a language stay as they
  are.

The parent of a translated page is the counterpart of the source's parent
(the French root for a top-level page).

## 3. Create the copies, inactive, in one batch

```json
{"operations": [
  {"op": "create", "resource": "pages", "ref": "angebot",
   "fields": {"title": "Notre offre", "slug": "offre", "parent": 5,
              "language_code": "fr", "translation_of": 17,
              "page_type": "standard", "is_active": false},
   "content": {"main": [
     {"type": "pages.richtext", "fields": {"text": "<p>…</p>"}},
     {"type": "pages.button", "fields": {"button_label": "Contact",
                                         "button_page": 45}}
   ]}},
  {"op": "create", "resource": "pages",
   "fields": {"title": "…", "parent": {"type": "pages.page", "id": {"ref": "angebot"}},
              "translation_of": 18, "language_code": "fr", "is_active": false}}
]}
```

- New plugins have no `id`; `depth` as in the source.
- Copies are inactive (`is_active: false`): editors review them in the
  admin and activate them.
- `{"ref": …}` lets a page refer to another page created in the same batch.

```bash
f3api batch translation.json            # a dry run: errors with their paths, warnings
f3api batch translation.json --apply    # all of it, or nothing
```

## Updating an existing translation

Read both, translate what changed in the source, and write the target with
`f3api update pages <id> --apply` (the ETag of what was read is sent, so a
change an editor made in the meantime isn't overwritten). `content`
replaces whole regions; `update-plugin` changes one plugin.
