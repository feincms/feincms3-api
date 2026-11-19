import re
from datetime import datetime
from pathlib import Path


DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
GITHUB = "https://github.com/feincms/feincms3-api/"

project = "feincms3-api"
copyright = f"{datetime.now().year}, the feincms3-api developers"
release = re.search(
    r'__version__ = "([^"]+)"', (ROOT / "feincms3_api" / "__init__.py").read_text()
)[1]

extensions = ["myst_parser"]
master_doc = "index"
exclude_patterns = ["_build"]
myst_heading_anchors = 4
# Some examples are JSON with "…" or an HTTP status line.
suppress_warnings = ["misc.highlighting_failure"]

html_title = f"feincms3-api {release}"
html_theme = "furo"
html_theme_options = {
    "source_repository": GITHUB,
    "source_branch": "main",
    "source_directory": "docs/",
}


# The Markdown files are written to be read on GitHub, and some of them live
# outside docs/ (pulled in with {include}). Their links point to files in the
# repository; point them to the pages here instead, or to GitHub.
PAGES = {
    "README.md": "index.md",
    "specs/api.md": "spec.md",
    "cli/README.md": "cli.md",
    "CHANGELOG.md": "changelog.md",
    "cli/CHANGELOG.md": "cli-changelog.md",
    **{f"docs/{p.name}": p.name for p in DOCS.glob("*.md")},
}
LINK = re.compile(
    r"\]\((?P<target>(?:" + re.escape(GITHUB) + r"blob/main/)?[^)#\s:]*)(?P<anchor>#[^)\s]*)?\)"
)


def rewrite_links(text, path):
    def replace(match):
        target, anchor = match["target"], match["anchor"] or ""
        if not target:
            return match[0]
        if target.startswith(GITHUB):
            file = target.removeprefix(f"{GITHUB}blob/main/")
        else:
            file = (path.parent / target).resolve().relative_to(ROOT).as_posix()
        if file in PAGES:
            return f"]({PAGES[file]}{anchor})"
        return f"]({GITHUB}blob/main/{file}{anchor})"

    return LINK.sub(replace, text)


def source_read(app, docname, source):
    source[0] = rewrite_links(source[0], Path(app.env.doc2path(docname)))


def include_read(app, relative_path, parent_docname, content):
    content[0] = rewrite_links(content[0], (DOCS / relative_path).resolve())


def setup(app):
    app.connect("source-read", source_read)
    app.connect("include-read", include_read)
