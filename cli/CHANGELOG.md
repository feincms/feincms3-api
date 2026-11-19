# Change log

## Next version

## 0.5 (2026-11-19)

- Needs feincms3-api 0.5 or later on the server (the resources are below
  `resources/`).
- `f3api move RESOURCE ID POSITION [RELATIVE_TO]` moves a node in its
  tree.
- `find` uses the resource with paths, if there is only one, instead of
  `pages`.
- `f3api site` is now `f3api sites`, like `tokens`. The help and the
  README say which manages the sites on this machine and which the tokens
  on the server.
- `f3api logout` is now `f3api sites logout [NAME]`.
- `sites add` checks the token before adding the site (`--no-check` if
  the site can't be reached yet).

## 0.4 (2026-11-19)

- `f3api tokens` (or `tokens list`) lists your tokens, `f3api tokens
  delete ID …` revokes them, `f3api logout` revokes the token in use and
  removes the site. Needs feincms3-api with `/tokens/`.

## 0.3 (2026-11-17)

- Built on click and rich: `site list`, `resources` and `list` print
  tables in a terminal, JSON with `--json` or when the output isn't a
  terminal. `-h` works as `--help`.
- `f3api skills list|install|uninstall`: Claude Code skills shipped with
  the CLI, linked into `~/.claude/skills` (or `.claude/skills` with
  `--project`).
- The `feincms3-translate` skill: translating pages and subtrees with
  `f3api`, as inactive copies in one batch.
- The `feincms3-dummy-content` skill: a page per page type with every
  plugin, text in the site's language (written as Markdown, converted at
  the end) and images from picsum.photos, built by a script it writes. It
  reads the project's renderer for layouts where the code is at hand.
- The `feincms3-review` skill: reviewing content and reporting what's
  broken, missing or badly written; links to the site's own host count as
  internal ones.
- An error answer which isn't JSON (a site's HTML 404 page) is shown as
  its status, reason and URL, not as its HTML.
- Error answers are closed, no `ResourceWarning` on Python 3.14 and
  later.

## 0.2 (2026-11-17)

- The version follows feincms3-api's: 0.2 talks to feincms3-api 0.2.

## 0.0.2 (2026-11-17)

- Fixed: a batch deleting an object it created crashed after saving;
  `content` took a region called `content` for the wrapper.
- Redirects aren't followed (they turned writes into reads and sent the
  token along): the error names the URL to use instead. Connection errors
  and answers which aren't JSON are errors, not tracebacks.
- `--site` wins over `F3API_URL`, and `F3API_TOKEN` no longer replaces the
  token of a configured site.
- The sites file is created readable by you only, not made so after
  writing it.
- The command is also installed as `feincms3-api-cli`, so
  `uvx feincms3-api-cli` works. Usage and messages use the name it was run
  as.

## 0.0.1 (2026-11-17)

- Initial release: the content API from the command line (`f3api`).
