---
name: feincms3-dummy-content
description: >
  Fill a feincms3 site with dummy content over its content API with the
  f3api CLI: a page per page type with every plugin its regions allow,
  plausible text in the site's language, placeholder images, all below one
  page so it can be removed again. Use when the user wants dummy, sample,
  test or placeholder content, a kitchen sink or style guide page, or
  wants to try templates and plugins with content.
---

# Dummy content for a feincms3 site with f3api

Dummy content shows how templates and plugins look with content: every
page type, every plugin, realistic lengths. It all goes **below one page**
(the dummy root), created in **one batch** after a **dry run**, and its ids
are written down, so it can be removed again completely.

`f3api` prints JSON when its output isn't a terminal. Writes are dry runs
unless `--apply` is given.

## 1. Agree on the job first

Ask, unless the user said so already:

- **The site**: `f3api site list`; dummy content belongs on a development
  or staging site. On a production site, only if the user insists.
- **Language** (`language_code`) and **where**: below which page the dummy
  root goes (`f3api find /de/`), or as a root of its own.
- **Active or inactive**: active pages can be looked at on the site, but
  are also in its navigation and sitemap. Inactive is the default on a
  site visitors can see.
- **What**: all page types and plugins (default), or some. Other resources
  (articles, …) too?
- **Images**: a folder the user names, or placeholders you make (step 3).

## 2. Read the schema

```bash
f3api resources
f3api schema                       # region sets: page types, regions, plugins per region
f3api schema pages.page            # the page's fields
f3api schema pages.richtext        # each plugin type
```

`f3api schema` lists the region sets (for pages, one per page type) with
the plugins each region allows. Each type's schema says which fields are
required, their kinds, `enum` values, lengths, and for `html` fields which
tags are allowed (`x-feincms3-html`).

Skip page types which are applications (a second instance of an app is
refused) unless the user asks for them, and say so.

The schema doesn't say how plugins are combined on the page. If the
project's code is at hand, read its renderer (`RegionRenderer`
registrations: subregions, marks, plugins which group consecutive ones or
set the layout of the plugins after them) and the page types (`TYPES`:
which are applications), and build content which shows each layout.

## 3. Images and files

For each image or file field (`{"upload": …}` in writes), upload a file
first and use the id it returns. Without a folder from the user, download
photos from picsum.photos in the sizes the site probably uses (wide for
headers, square for teasers and people); a seed in the URL gives the same
image each time:

```bash
curl -sSfL --retry 3 --max-time 30 -o header-1.jpg https://picsum.photos/seed/header-1/1600/900
f3api upload header-1.jpg          # prints {"upload": "…", "name": …}
```

The API doesn't fetch URLs itself; download, then upload. Uploads are used
once: upload one file per field (a dry run doesn't use them up). Other
files: a PDF or a short test video where a tool for it is at hand
(ImageMagick, ffmpeg), else small files you write. Cabinet files (`cabinet.file` references) are
created as objects of the `files` resource with their upload, then
referenced by id; put them into one folder named like the dummy root.

## 4. Build the batch

One create for the dummy root, then one page per page type below it
(`parent` is `{"type": "pages.page", "id": {"ref": "dummy"}}`), each with
content in every region:

- **Every plugin** the region allows, at least once; containers with
  children (`depth`).
- **Plausible text** in the site's language, matching the field: short
  titles, a few sentences in leads, several paragraphs with headings and
  lists in rich text. Vary the lengths: one plugin short, one long, to
  show how the layout copes. No "Lorem ipsum" unless the user wants it.
- **Write `html` fields as Markdown** and convert them to HTML only when
  writing the batch file (pandoc, or Python's `markdown` package, e.g.
  with `uv run --with markdown`): shorter to write than HTML. Don't fuss
  over the tags `x-feincms3-html` allows: it's dummy content, and where
  the field is `sanitized`, the server drops what isn't allowed.
- **Choices**: valid values (`enum`); show different ones in different
  plugins.
- **Links and references**: to other dummy pages (by `ref`) and to
  existing pages, so link styles show. References to other objects
  (projects, people, forms) go to existing ones; pick active ones
  (`f3api list projects -f is_active=true --fields title`), since a field
  may only accept those, which the schema doesn't show.
- **Fields**: all required ones (`required` in the schema), and the
  optional ones filled too, so templates show them. Slugs from the title.
- `is_active` as agreed; the same for all, so nothing dummy is active by
  accident below an inactive root.

```json
{"operations": [
  {"op": "create", "resource": "pages", "ref": "dummy",
   "fields": {"title": "Dummy content", "slug": "dummy-content", "parent": 3,
              "page_type": "standard", "language_code": "de",
              "is_active": false}},
  {"op": "create", "resource": "pages", "ref": "standard",
   "fields": {"title": "Standard page", "slug": "standard",
              "parent": {"type": "pages.page", "id": {"ref": "dummy"}},
              "page_type": "standard", "language_code": "de",
              "is_active": false},
   "content": {"main": [
     {"type": "pages.richtext", "depth": 0,
      "fields": {"text": "<h2>Was wir tun</h2><p>Wir planen, bauen und betreuen Websites.</p>"}},
     {"type": "pages.button", "depth": 0,
      "fields": {"button_label": "Kontakt", "button_page": 3}}
   ]}}
]}
```

A batch has at most 100 operations; split larger ones, the dummy root in
the first.

Rather than writing the JSON by hand, write a small script which builds
it: the texts as Markdown, converted to HTML; downloads and uploads,
caching the downloaded files so a second run only uploads; the batch file
at the end. Keep it, to build the content again after changes.

## 5. Dry run, then apply

```bash
f3api batch dummy.json             # dry run: errors with their paths, warnings
```

Fix what the errors name and run again until it passes; check
`meta.warnings`. Then apply:

```bash
f3api batch dummy.json --apply
```

Write the created ids (`refs` and `results` of the answer) to a file,
e.g. `dummy-content-ids.json`, for removing it later.

## 6. Report

The dummy root's path and `meta.admin_url`, the pages with their page
types, plugins which couldn't be created and why, and page types skipped.

## Removing it again

Pages with children can't be deleted, so delete bottom-up, the dummy
root last, then the uploaded files and their folder:

```bash
f3api list pages -f parent=40 --fields title,path
f3api get pages 41
f3api delete pages 41              # dry run
f3api delete pages 41 --apply
```

Or all of it in one batch, children first, each `delete` with the `etag`
from a fresh `get`. Delete only what's below the dummy root or in the ids
file: never other content.
