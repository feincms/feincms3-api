# feincms3-api-cli

The [feincms3 content API](https://github.com/feincms/feincms3-api) from the
command line, for people and for agents with a shell. It talks HTTP with a
token, so it works against any site with the API, without the site's code.

```bash
uvx feincms3-api-cli --help        # or: uv tool install feincms3-api-cli
```

Installed, the command is also `f3api`, which the examples use; with `uvx`,
write `uvx feincms3-api-cli` instead.

## Sites

A site is the API's URL and a token (create one in the admin, or with
`./manage.py f3api_token`). `f3api sites` keeps them on this machine, in
`~/.config/f3api/sites.json` (readable by you only):

```bash
f3api sites add example https://example.com/api/   # asks for the token, checks it
f3api sites list
f3api sites default example
f3api --site other list pages.page                 # the first site is the default
f3api sites logout example                         # revokes the token, removes the site
```

`sites logout` revokes the token on the server too (`sites remove` only
forgets it); a token which has expired already counts as revoked.

`F3API_URL` and `F3API_TOKEN` replace the default site (not one named with
`--site`), `F3API_SITE` names the default.

## Tokens

`f3api tokens` manages your tokens on the server of the site in use:

```bash
f3api tokens                # lists them, * marks the one in use
f3api tokens delete 12 13   # revokes them, e.g. one which leaked
```

Revoking the token in use removes the site, like `sites logout` (or tells
you to unset `F3API_TOKEN`).

## Reading

Reads print JSON. `sites list`, `resources` and `list` print tables in a
terminal, and JSON with `--json` or when the output goes to a pipe or a
file (scripts, agents):

```bash
f3api resources
f3api schema                       # region sets and the plugins allowed per region
f3api schema pages.page            # one type's fields
f3api list pages.page -f language_code=de -q angebot --fields title,path
f3api get pages.page 17 --expand   # with meta.references
f3api find /de/angebot/            # in the resource with paths, or --resource
f3api plugin pages.page 17 pages.button 103
```

## Writing

Writes are **dry runs unless `--apply`** is given: run once, look at the
result and its `meta.warnings`, then run again with `--apply`.

Writes send the ETag of the object as it was last read with `f3api`
(`get`, `find`, `list --content`), so a write fails with exit code 4 if
someone changed the object since, instead of overwriting their change.
Read it again, or pass `--etag` or `--force`.

```bash
f3api get pages.page 17 > page.json
f3api update pages.page 17 <<'EOF'
{"fields": {"title": "Unser Angebot"}}
EOF
f3api update pages.page 17 changes.json --apply
f3api content pages.page 17 regions.json --apply        # replace the regions given
f3api update-plugin pages.page 17 pages.button 103 --apply <<'EOF'
{"button_label": "Kontakt"}
EOF
f3api create pages.page new-page.json --apply
f3api delete articles.article 3 --apply
f3api cache-clear pages.page 17
f3api upload team.jpg              # prints {"upload": "…"} to use in fields
f3api batch subtree.json --apply   # several writes in one transaction
```

In a batch, operations without an `etag` get the one last read.

## Skills

The CLI brings [Claude Code](https://claude.com/claude-code) skills using
`f3api`, and links them into Claude Code's skills:

```bash
f3api skills list                  # the skills and whether Claude Code has them
f3api skills install               # link them into ~/.claude/skills
f3api skills install --project     # … or into .claude/skills here
f3api skills uninstall
```

The links point to where the CLI is installed, so upgrading it upgrades
the skills; run `install` again when an upgrade brings new ones.
`CLAUDE_CONFIG_DIR` moves `~/.claude`.

The skills:

- `feincms3-translate`: translate a page or a subtree into another
  language, as inactive copies for the editors to review.
- `feincms3-dummy-content`: a page per page type with every plugin, below
  one page which can be removed again.
- `feincms3-review`: check content for broken references and links,
  missing translations and alt texts, spelling and register; a report,
  fixes only when asked.

## Errors

Errors go to stderr with their paths:

```
f3api: 422 The data isn't valid.
  fields.slug: This field is required.
  content.main[2].fields.text: …
```

Exit codes: 0 done, 1 failed, 2 usage, 3 invalid data, 4 conflict (ETag,
references), 5 not found, 6 not allowed.
