---
name: feincms3-review
description: >
  Review the content of a feincms3 site over its content API with the f3api
  CLI: broken references and links, links to inactive pages, missing
  translations, empty regions, missing meta descriptions and alt texts,
  spelling, register and terminology, leftover placeholder text. Reports
  with admin links; changes only when asked. Use when the user wants to
  review, check, audit, proofread or clean up the content of a feincms3
  site, a page or a subtree.
---

# Reviewing feincms3 content with f3api

A review **reads** and **reports**. It changes nothing unless the user asks
for a fix afterwards, and then each fix is a dry run first.

`f3api` prints JSON when its output isn't a terminal.

## 1. Agree on the scope

Ask, unless the user said so already:

- **The site**: `f3api sites list`.
- **What**: the whole site, one language, a subtree (`f3api find /de/angebot/`),
  or other resources (articles, …).
- **Which checks** (step 4): all of them (default), or e.g. only
  proofreading.
- **Glossary and register**: terms with a fixed spelling, formal or
  informal address. Ask, or read a file the user names.
- **External links**: checking them means requests to other sites; only
  when the user agrees.

## 2. Read the schema

```bash
f3api resources
f3api schema pages.page
```

The kinds say what to proofread: fields marked `text` and `html` are
content; `machine` values aren't. `required` and the page's fields (a
`meta_description`, …) say what a page should have; types differ between
sites, so go by the schema, not by assumptions.

## 3. Collect the pages

Walk the tree from the start page, a level at a time:

```bash
f3api list pages.page -f parent=17 --fields title,path,is_active,language_code --limit 200
```

or list a language at once:

```bash
f3api list pages.page -f language_code=de --fields title,path,is_active --limit 200
```

Then read each page with its references:

```bash
f3api get pages.page 17 --expand
```

For a large site, review subtree by subtree (or hand subtrees to
subagents, each returning its findings), and collect the findings.

## 4. The checks

Structure and references:

- **Broken references**: a reference whose entry in `meta.references` is
  `null` points to something which doesn't exist (or which the token may
  not see).
- **Links to inactive pages**: references to and internal links to pages
  with `is_active: false`, from active pages
  (`f3api list pages.page -f is_active=false --fields title,path --limit 200`).
- **Internal links** in `html` fields, `link` fields and JSON data: paths
  (`/de/…`) and absolute URLs to the site's own host (the host of the
  pages' `meta.url`). Check each path once with
  `f3api find /de/kontakt/`; a 404 is a broken link. Links to files
  (`/media/…`) are checked like external links.
- **External links**: only if agreed (HEAD requests, slowly, each URL
  once).
- **Warnings**: `meta.warnings` of each object (e.g. plugins in regions
  the page type doesn't have).

Completeness:

- **Empty pages and regions**: active pages without content, regions
  without plugins where the page type has them.
- **Missing fields**: empty meta descriptions, leads, titles which are
  only the slug.
- **Images**: alt texts (cabinet files' `alt_text` in `meta.references`,
  image plugins' alt fields), very large or tiny images (`width`,
  `height`, `size`).
- **Translations**: for each page in the main language,
  `f3api list pages.page -f translation_of=17 --fields language_code,title,path`;
  list what's missing per language, and translations whose `is_active`
  differs from the source's.
- **Leftovers**: placeholder text ("Lorem ipsum", "TODO", "xxx", "Dummy"),
  test pages, outdated years and dates, duplicate titles in one language.

Language, in the fields the kinds mark `text` and `html`:

- Spelling and grammar.
- Register: formal or informal address used consistently; the glossary's
  terms.
- Consistency across pages: the same thing named the same way, the
  company's name spelled the same, button labels in the same style.
- Markup in `html` fields: empty paragraphs, headings out of order (an h4
  after an h2), text pasted with leftover formatting.

## 5. Report

Group the findings by page (title, path, `meta.admin_url`), most important
first:

1. Broken: references, links, pages linking to inactive pages.
2. Missing: content, translations, alt texts, meta descriptions.
3. Language: each finding with the field's path
   (`content.main[2].fields.text`), the passage and a suggestion.

End with a summary: pages reviewed, findings per kind, what wasn't
checked (external links, resources left out).

## 6. Fixes, only when asked

Fix what the user picks, page by page. Read the page again right before
changing it (`f3api get pages.page 17`), so the write sends a current ETag:

```bash
f3api update-plugin pages.page 17 pages.richtext 101 fix.json   # one plugin: dry run
f3api update-plugin pages.page 17 pages.richtext 101 fix.json --apply
f3api update pages.page 17 changes.json --apply                  # fields of the page
```

A write fails with exit code 4 if an editor changed the page in the
meantime: read it again and redo the fix. Language fixes keep the markup
and change only the text in question; leave content decisions (what to
write where something is missing) to the editors unless the user asks.
