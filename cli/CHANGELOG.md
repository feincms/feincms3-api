# Change log

## Next version

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
