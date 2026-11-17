# Change log

## Next version

- Built on click and rich: `site list`, `resources` and `list` print
  tables in a terminal, JSON with `--json` or when the output isn't a
  terminal. `-h` works as `--help`.
- `f3api skills list|install|uninstall`: Claude Code skills shipped with
  the CLI, linked into `~/.claude/skills` (or `.claude/skills` with
  `--project`).
- The `feincms3-translate` skill: translating pages and subtrees with
  `f3api`, as inactive copies in one batch.

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
