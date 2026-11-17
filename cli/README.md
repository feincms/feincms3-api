# feincms3-api-cli

The [feincms3 content API](https://github.com/feincms/feincms3-api) from the
command line, for people and for agents with a shell. It talks HTTP with a
token, so it works against any site with the API, without the site's code.
Standard library only.

```bash
uvx feincms3-api-cli --help        # or: uv tool install feincms3-api-cli
```

Installed, the command is also `f3api`, which the examples use; with `uvx`,
write `uvx feincms3-api-cli` instead.

## Sites

A site is the API's URL and a token (create one in the admin, or with
`./manage.py f3api_token`):

```bash
f3api site add example https://example.com/api/   # asks for the token
f3api site list
f3api --site other list pages                     # the first site is the default
```

`F3API_URL` and `F3API_TOKEN` replace the default site (not one named with
`--site`), `F3API_SITE` names the default. Sites are kept in
`~/.config/f3api/sites.json` (readable by you only).

## Reading

Everything prints JSON:

```bash
f3api resources
f3api schema                       # region sets and the plugins allowed per region
f3api schema pages.page            # one type's fields
f3api list pages -f language_code=de -q angebot --fields title,path
f3api get pages 17 --expand        # with meta.references
f3api find /de/angebot/
f3api plugin pages 17 pages.button 103
```

## Writing

Writes are **dry runs unless `--apply`** is given: run once, look at the
result and its `meta.warnings`, then run again with `--apply`.

Writes send the ETag of the object as it was last read with `f3api`
(`get`, `find`, `list --content`), so a write fails with exit code 4 if
someone changed the object since, instead of overwriting their change.
Read it again, or pass `--etag` or `--force`.

```bash
f3api get pages 17 > page.json
f3api update pages 17 <<'EOF'
{"fields": {"title": "Unser Angebot"}}
EOF
f3api update pages 17 changes.json --apply
f3api content pages 17 regions.json --apply        # replace the regions given
f3api update-plugin pages 17 pages.button 103 --apply <<'EOF'
{"button_label": "Kontakt"}
EOF
f3api create pages new-page.json --apply
f3api delete articles 3 --apply
f3api cache-clear pages 17
f3api upload team.jpg              # prints {"upload": "…"} to use in fields
f3api batch subtree.json --apply   # several writes in one transaction
```

In a batch, operations without an `etag` get the one last read.

## Errors

Errors go to stderr with their paths:

```
f3api: 422 The data isn't valid.
  fields.slug: This field is required.
  content.main[2].fields.text: …
```

Exit codes: 0 done, 1 failed, 2 usage, 3 invalid data, 4 conflict (ETag,
references), 5 not found, 6 not allowed.
