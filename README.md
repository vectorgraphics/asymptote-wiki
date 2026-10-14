# asymptote-wiki

A lightweight wiki for sharing Asymptote examples. Users upload one or more
`.asy` files along with markdown prose; the server renders the Asymptote code
using the locally installed `asy` (or the `asymptote-http-server` on port
10007) and displays the results inline.

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
     render/        # cached SVG/PDF/HTML output
         main.asy.svg
         main.asy.pdf
```

No database required. Back up by copying `data/`.

## Deployment (production)

See `deploy/` for the systemd unit and nginx configuration snippet.

## Rendering

The wiki invokes the `asy` binary directly, with the example's directory
as the working directory. This means multi-file examples work naturally --
if `main.asy` does `input("data.txt")` or `import "helper"`, those files
are found alongside it.

```
cd data/pages/0001-my-example/
asy -f svg -o render/main.asy.svg main.asy
```

Supported output formats: `svg`, `pdf`, `html`, `v3d`, `eps`.
Render timeout defaults to 30 s. Max source size 100 KB per file.

## License

GNU Lesser General Public License, version 3 or later (same as Asymptote).

