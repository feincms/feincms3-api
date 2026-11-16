# Change log

## Next version

- Added the read side of the content API: `ContentAPI` with `PageResource`
  and `ObjectResource`, forms, permissions and plugins taken from the admin,
  `GET` lists and details with nested plugin content, `/schema/` with the
  kind of every string, and the `f3api_schema` snapshot command.
- Started feincms3-api from feincms3-autotranslate. The translation pipeline
  lives on in `feincms3_api.incubator.autotranslate`, its app label stays
  `feincms3_autotranslate`.
- feincms3-autotranslate: dump feincms3 content with feincms3-data, translate
  it through a file-based workflow or the Claude API, and load the copies as
  inactive objects.
