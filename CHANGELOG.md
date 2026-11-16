# Change log

## Next version

- Added the write side: `POST` to create objects with their content,
  `PATCH` for fields and regions, `PUT …/content/` for regions, `PATCH` for
  one plugin, `DELETE`. Writes go through the admin's forms, need
  `If-Match` with the ETag, fail as a whole with every problem and its
  path, can be dry runs (`dry_run=true`), and are logged in the admin's
  history.
- Added API tokens (`APIToken`): bearer authentication before session
  authentication, read-only or read-write, expiring after 8 hours by
  default (`TOKEN_LIFETIME`, a `timedelta`). Staff users create their own in
  the admin, which shows the secret once, or with `./manage.py f3api_token`.
- Added the read side of the content API: `ContentAPI` with `PageResource`
  and `ObjectResource`, forms, permissions and plugins taken from the admin,
  `GET` lists and details with the plugins flat with their depth (or
  nested with `nested=true`), list filters declared on the resource,
  `/schema/` with the
  kind of every string, and the `f3api_schema` snapshot command.
- Started feincms3-api from feincms3-autotranslate. The translation pipeline
  lives on in `feincms3_api.incubator.autotranslate`, its app label stays
  `feincms3_autotranslate`.
- feincms3-autotranslate: dump feincms3 content with feincms3-data, translate
  it through a file-based workflow or the Claude API, and load the copies as
  inactive objects.
