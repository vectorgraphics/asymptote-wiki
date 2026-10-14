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

python app.py
# -> http://localhost:8080
```

## Storage

Pages are stored as plain directories under `data/pages/`:

```
data/pages/
 0001-example-name/
     page.md        # markdown with ```asy:filename.asy fences
     meta.json      # {title, author, email, date, tags}
     main.asy       # source files
     helper.asy
     data.txt       # optional non-asy data files
     output/        # compiled HTML (inline SVG for 2D, WebGL for 3D)
         main.asy.html
         helper.asy.html
```

No database required. Back up by copying `data/`.

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

GNU Lesser General Public License, version 3 or later (same as Asymptote).

