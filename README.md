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

The wiki calls `localhost:10007` (or invokes `asy` directly) to render
submissions. It does not modify or depend on the Asymptote source tree.

## Quick Start (development)

```bash
cd asymptote-wiki
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Point the app at your local asy installation or http-server:
export ASY_BINARY=$(which asy)
# or:
export ASY_HTTP_URL=http://localhost:10007

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

By default the wiki shells out to the `asy` binary:

```
asy -f svg -o render/main.asy.svg main.asy
```

Set `ASY_HTTP_URL` to instead POST to the `asymptote-http-server`, which
reuses its configured Asymptote environment:

```
POST http://localhost:10007?f=svg
Content-Type: application/octet-stream
<body: raw .asy source>
```

Render timeout defaults to 30 s. Max source size 100 KB per file.

## License

GNU Lesser General Public License, version 3 or later (same as Asymptote).

