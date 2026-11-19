---
name: feincms3-translate
description: >
  Translate content of a feincms3 site over its content API with the f3api
  CLI: a page, a subtree of pages, an article or other objects with a
  language, into another language, as inactive copies linked with
  translation_of for editors to review. Use when the user wants to
  translate pages or content of a feincms3 site, find out what isn't
  translated yet, or update an existing translation.
---

# Translating feincms3 content with f3api

Translations are copies: a new object per source object, in the target
language, with `translation_of` pointing to the source, created
**inactive** in **one batch**, after a **dry run**. Editors review and
activate them in the admin. The site needs no translation feature; this
is reads, the schema, and `f3api batch`.

`f3api` prints JSON when its output isn't a terminal. Writes are dry runs
unless `--apply` is given.

## 1. Agree on the job first

Ask, unless the user said so already:

- **The site**: `f3api sites list` (`--site NAME` for another than the
  default), and `f3api resources` for what it exposes.
- **The source**: a page (`f3api find /de/angebot/`), a subtree (the page
  and everything below it), or other objects (articles, …).
- **Source and target language** (`language_code` values, e.g. `de`, `fr`).
- **Glossary and register**: terms with a fixed translation, formal or
  informal address, tone. Ask, or read a file the user names. Don't
  invent a glossary.
- **Existing translations**: skip them (default) or update them.

## 2. Read the schema

```bash
f3api schema                       # region sets, plugins allowed per region
f3api schema pages.page            # one type's fields
```

Read the schema of each type you're going to copy: the object's type and
every plugin and inline type it has. Every string field has an
`x-feincms3-kind`:

| kind | what to do |
|---|---|
| `text` | translate |
| `html` | translate the text, keep the markup; `x-feincms3-html` says which tags the field allows |
| `link` | map to the counterpart in the target language (step 5), keep external URLs |
| `machine` | keep as it is (except `slug`, below) |
| `choices` | `value\|label` lines: translate the labels, keep the values |

Fields without a kind (numbers, booleans, dates, references) are kept,
apart from references, which are mapped (step 5).

## 3. Select what to translate

For a subtree, walk it, parents before children:

```bash
f3api list pages.page -f parent=17 --fields title,path --limit 200
```

Lists of trees are in tree order, so `-f parent=` lists the children in
their order, also where pages have no `position` field. Repeat for each
child. Other resources with `language_code` and
`translation_of` filters (see `f3api schema` and the resource's list) work
the same way, without the tree.

Then find what's translated already, per object:

```bash
f3api list pages.page -f translation_of=17 -f language_code=fr --fields title,path
```

Skip objects which have a translation, and say so; update one only when
the user asked for it (step 8).

## 4. Turn each source object into a create operation

```bash
f3api get pages.page 17 --expand        # fields, content, inlines, meta.references
```

From what `get` prints, build `{"op": "create", "resource": "pages.page", "ref":
…, "fields": …, "content": …, "inlines": …}`:

- **Drop** `id`, `type` and `meta` of the object, fields the schema marks
  `readOnly` (e.g. `path`), and the `id` of every plugin in `content` and
  of every object in `inlines`. Keep each plugin's `type`, `depth` and
  order.
- **Translate** the fields the kinds mark (step 2), in `fields`, in each
  plugin's `fields` and in `inlines`.
- **Set** `language_code` to the target, `translation_of` to the source's
  id, `is_active` to `false`, and `parent` to the counterpart of the
  source's parent (the target language's root for a top-level page; a
  `{"ref": …}` for a parent created in the same batch).
- **Slug**: from the translated title (lowercase, ASCII, hyphens).
- **Static paths** (`static_path: true`): don't translate the path; create
  the copy without it and list it for the editors.
- Copy whole objects, all regions, not only their strings.

`meta.references` describes what the object refers to (title, path), which
helps with step 5.

## 5. Map links and references

References (`{"type": …, "id": …}`, and ids JSON data marks with
`x-feincms3-ref`) and internal links (`link` fields, links in `html`) point
to objects in the source language. For each, find the counterpart:

```bash
f3api list pages.page -f translation_of=44 -f language_code=fr --fields title,path
f3api find /de/kontakt/            # an internal link's page, then as above
```

- A counterpart exists: use its id, or its path for links.
- It's created in this batch: `{"type": "pages.page", "id": {"ref": "kontakt"}}`.
- None exists: keep the reference and list it for the editors.
- Files (`cabinet.file`) and objects without a language stay as they are.

## 6. One batch, dry run first

Put all operations into one file, parents before children, and siblings
in their source order: a new page goes last among its siblings unless the
schema has `position` and it's set. To compare the order with an existing
translation, list both parents' children (`f3api list pages.page -f
parent=… --fields title,translation_of`).

```json
{"operations": [
  {"op": "create", "resource": "pages.page", "ref": "angebot",
   "fields": {"title": "Notre offre", "slug": "offre", "parent": 5,
              "language_code": "fr", "translation_of": 17,
              "page_type": "standard", "is_active": false},
   "content": {"main": [
     {"type": "pages.richtext", "depth": 0, "fields": {"text": "<p>Ce que nous faisons</p>"}},
     {"type": "pages.button", "depth": 0, "fields": {"button_label": "Contact",
                                                     "button_page": 45}}
   ]}},
  {"op": "create", "resource": "pages.page",
   "fields": {"title": "Formations", "slug": "formations",
              "parent": {"type": "pages.page", "id": {"ref": "angebot"}},
              "language_code": "fr", "translation_of": 18,
              "page_type": "standard", "is_active": false}}
]}
```

```bash
f3api batch translation.json             # dry run: errors with their paths, warnings
```

Fix what the errors name (`operations[1].fields.slug: …`) and run the dry
run again until it passes; look at `meta.warnings` too. Then show the user
what will be created and apply only when they agree:

```bash
f3api batch translation.json --apply     # all of it, or nothing
```

Create the copies active only if the user explicitly says so.

## 7. Objects with a region per language

Some objects keep all languages in one object, a region per language (a
tab list shared between the languages of a page). There, the translation
is the target language's region, written with `content`:

```bash
f3api get tab-lists 7
f3api content tab-lists 7 regions.json            # dry run
f3api content tab-lists 7 regions.json --apply
```

`content` replaces the regions given and leaves the others alone. A
region has no `is_active`: it's **live immediately**. Say so and ask
before applying.

## 8. Updating an existing translation

Only when asked. Read both (`f3api get pages.page 17`, `f3api get pages.page 31`),
translate what changed in the source, and write the target:

```bash
f3api update pages.page 31 changes.json                # dry run
f3api update pages.page 31 changes.json --apply
```

`f3api` sends the ETag of what it read, so a change an editor made in the
meantime makes the write fail (exit code 4) instead of being overwritten:
read again and redo the change. `update` with `content` replaces whole
regions; `update-plugin` changes one plugin.

## Large jobs

For a large subtree, translate page by page (or hand pages to subagents,
each returning its create operations), and collect everything into one
batch at the end, so it's all or nothing.

## Finish with a report

- What was created (title, path, `meta.admin_url` from the batch result),
  inactive, to be reviewed and activated in the admin.
- What was skipped because it was translated already.
- References and links which couldn't be mapped, and static paths, for
  the editors.
