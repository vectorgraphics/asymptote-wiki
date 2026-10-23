# asymptote-wiki

A lightweight wiki for sharing Asymptote examples. Users upload one or more
`.asy` files along with markdown prose; the server compiles the Asymptote
code using the locally installed `asy` and displays the results inline
(inline SVG for 2D, interactive WebGL for 3D).

Complements the [Asymptote Web Application](https://asymptote.ualberta.ca)
(single-page playground) by providing a persistent, multi-page, multi-file
gallery of contributed examples.

## Architecture

```
asymptote.ualberta.ca
 :443     Asymptote Web Application (existing)
 :8080    This wiki (new, behind nginx /wiki/)
 :10007   Asymptote HTTP render server (existing)
```

The wiki invokes the local `asy` binary directly, running each example
in its own directory so multi-file examples (imports, data files,
modules) work naturally. It does not modify or depend on the Asymptote
source tree.

## Quick Start (development)

```bash
cd asymptote-wiki
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Point the app at your asy installation:
export ASY_BINARY=$(which asy)

# Set a session/CSRF secret (required):
export WIKI_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

python app.py
# -> http://localhost:8080
```
## Git Tracking

Every create, edit, delete, and file operation on pages is automatically
committed to a git repository in `data/pages/`. This provides an audit trail
and protects against accidental or malicious data loss -- any page can be
restored from the git history at any time.

Configuration (environment variables):

| Variable         | Default                | Purpose                          |
|------------------|------------------------|----------------------------------|
| `WIKI_GIT_USER`  | `asywiki`              | Git author name for commits      |
| `WIKI_GIT_EMAIL` | `asywiki@localhost`    | Git author email for commits     |
| `GITHUB_REMOTE`  | *(unset)*              | Remote URL to push to (optional) |

If `GITHUB_REMOTE` is set, the app will attempt `git push` after each commit
(best-effort -- push failures are logged but do not affect the wiki).

To inspect the history:

```bash
cd data/pages
git log --oneline
git show HEAD
git diff HEAD~1
```

To restore a deleted page:

```bash
cd data/pages
git checkout <commit> -- <path/to/page>
```

The data repo is public: [vectorgraphics/asymptote-wiki-data](https://github.com/vectorgraphics/asymptote-wiki-data).
Users can subscribe to commits or diff versions of any example's source.

## Search

The sidebar search box matches against page titles, author names, tags,
and the text content of all `.asy` source files. Results show which field
matched (e.g. "Matched: tag: parametric, source: main.asy").

## Security

- **CSRF protection** -- all POST forms require a token (Flask-WTF)
- **Rate limiting** -- 200 requests/hour per IP (Flask-Limiter); file-serving
  routes are exempt
- **Path traversal** -- all page paths are validated to stay within `data/pages/`
- **Compile sandbox** -- `asy` runs with `-noglobalread`; at most 3 concurrent
  compilations (configurable via `MAX_CONCURRENT_COMPILES`)
- **Debug mode** -- forbidden in production (raises at startup if enabled)
- **Secret** -- `WIKI_SECRET` must be set; known defaults are rejected in production

## Storage

Pages are stored as plain directories under `data/pages/`:

```
data/pages/
 0001-example-name/
     page.md        # markdown with ```asy:filename.asy fences
     meta.json      # {title, author, date, tags}
     main.asy       # source files
     helper.asy
     data.txt       # optional non-asy data files
     output/        # compiled HTML (gitignored -- build artifact)
         main.asy.html
         helper.asy.html
```

No database required. Back up by copying `data/` (the git repo in
`data/pages/` already tracks source files and provides history).

## Deployment (production)

See `deploy/` for the systemd unit and nginx configuration snippet.

## Compilation

The wiki invokes the `asy` binary directly, with the example's directory
as the working directory. This means multi-file examples work naturally --
if `main.asy` does `input("data.txt")` or `import "helper"`, those files
are found alongside it.

The default output format is `html`, which produces a self-contained HTML
file: inline SVG for 2D figures, or WebGL (via asygl.js) for interactive
3D scenes. This is the same approach used by the Asymptote Web Application.

```
cd data/pages/0001-my-example/
asy -f html -o output/main.asy.html main.asy
```

Other formats (`svg`, `pdf`, `v3d`, `eps`) can be requested per-fence
with ` ```asy:main.asy?f=svg `.
Compile timeout defaults to 30 s. Max source size 100 KB per file.

## License

Apache License 2.0

