"""
asymptote-wiki: A lightweight wiki for sharing Asymptote examples.

Entry point. Run with:
    python app.py

Configuration via environment variables:
    ASY_BINARY      Path to the asy executable (default: "asy")
    ASY_HTTP_URL    URL of asymptote-http-server (e.g. http://localhost:10007)
                    If set, renders via HTTP instead of shelling out.
    WIKI_DATA_DIR   Storage directory (default: ./data)
    WIKI_PORT       Port to listen on (default: 8080)
    WIKI_HOST       Host to bind (default: 127.0.0.1)
    RENDER_TIMEOUT  Seconds before a render is killed (default: 30)
    MAX_SOURCE_SIZE Max bytes per uploaded file (default: 100_000)
"""

import os
import re
import json
import shutil
import logging
from pathlib import Path
from datetime import datetime, timezone

from flask import (
    Flask, render_template, request, redirect, url_for,
    send_from_directory, abort, flash
)
import markdown

from wiki.render import render_asy
from wiki.storage import (
    list_pages, get_page, save_page, delete_page, next_id
)

# --- Configuration ---------------------------------------------------------

DATA_DIR = Path(os.environ.get("WIKI_DATA_DIR", Path(__file__).parent / "data"))
PAGES_DIR = DATA_DIR / "pages"
PAGES_DIR.mkdir(parents=True, exist_ok=True)

ASY_BINARY = os.environ.get("ASY_BINARY", "asy")
ASY_HTTP_URL = os.environ.get("ASY_HTTP_URL", "")
RENDER_TIMEOUT = int(os.environ.get("RENDER_TIMEOUT", "30"))
MAX_SOURCE_SIZE = int(os.environ.get("MAX_SOURCE_SIZE", "100_000"))

# --- App setup -------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("WIKI_SECRET", "dev-change-me")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB upload limit

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("asymwiki")

# --- Helpers ----------------------------------------------------------------

ASY_FENCE_RE = re.compile(
    r"```asy:(?P<file>\S+)(?:\?f=(?P<fmt>\w+))?\n(?P<code>.*?)(?P=closing)```",
    re.DOTALL
)
# Simpler: we match ```asy:filename  ...  ```
ASY_FENCE_RE = re.compile(
    r"```asy:(?P<file>[\w.\-]+)(?:\?f=(?P<fmt>\w+))?\s*\n(?P<code>.*?)```",
    re.DOTALL
)


def render_markdown_with_asy(md_text: str, page_dir: Path) -> str:
    """
    Render markdown, replacing ```asy:filename fences with the rendered output.
    The actual .asy source files live in page_dir; the fence content is
    displayed as collapsible source code.
    """
    def replace_fence(m):
        filename = m.group("file")
        fmt = m.group("fmt") or "svg"
        code = m.group("code")
        src_file = page_dir / filename

        # Determine render path
        render_file = page_dir / "render" / f"{filename}.{fmt}"
        if not render_file.exists():
            # Render it
            try:
                render_asy(
                    src_file=src_file,
                    out_file=render_file,
                    fmt=fmt,
                    timeout=RENDER_TIMEOUT,
                    asy_binary=ASY_BINARY,
                    http_url=ASY_HTTP_URL,
                )
            except Exception as e:
                return f"<pre class='render-error'>Render failed for {filename}:\n{e}</pre>"

        # Build the HTML snippet
        if fmt == "svg":
            svg_content = render_file.read_text()
            img_html = f"<div class='asy-render asy-svg'>{svg_content}</div>"
        elif fmt == "pdf":
            url = url_for("serve_render", page_id=page_dir.name, filename=f"{filename}.pdf")
            img_html = f"<div class='asy-render asy-pdf'><iframe src='{url}' width='100%' height='400'></iframe></div>"
        elif fmt in ("html", "v3d"):
            url = url_for("serve_render", page_id=page_dir.name, filename=f"{filename}.{fmt}")
            img_html = f"<div class='asy-render asy-html'><iframe src='{url}' width='100%' height='400'></iframe></div>"
        else:
            img_html = f"<p>[rendered {filename} as {fmt}]</p>"

        # Collapsible source
        escaped_code = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        src_html = f"<details class='asy-source'><summary>View source ({filename})</summary><pre>{escaped_code}</pre></details>"

        return img_html + src_html

    # Replace asy fences with rendered HTML
    html_with_asy = ASY_FENCE_RE.sub(replace_fence, md_text)

    # Render the rest as markdown (but don't re-process the HTML we inserted)
    # For MVP: render markdown first, then substitute. A more robust approach
    # would use markdown extensions or a custom preprocessor.
    rendered = markdown.markdown(html_with_asy, extensions=["fenced_code", "tables"])
    return rendered


# --- Routes -----------------------------------------------------------------

@app.route("/")
def index():
    """Gallery listing of all pages."""
    pages = list_pages(PAGES_DIR)
    return render_template("index.html", pages=pages)


@app.route("/page/<page_id>")
def view_page(page_id):
    """View a single example page."""
    page = get_page(PAGES_DIR, page_id)
    if page is None:
        abort(404)

    page_dir = PAGES_DIR / page_id
    md_file = page_dir / "page.md"
    md_text = md_file.read_text() if md_file.exists() else ""

    content_html = render_markdown_with_asy(md_text, page_dir)

    # List available source files
    source_files = [
        f.name for f in sorted(page_dir.iterdir())
        if f.is_file() and f.suffix in (".asy", ".txt", ".csv", ".dat")
        and f.parent == page_dir
    ]

    return render_template(
        "page.html",
        page=page,
        content_html=content_html,
        source_files=source_files,
        page_id=page_id,
    )


@app.route("/page/<page_id>/raw/<filename>")
def serve_source(page_id, filename):
    """Serve raw source file."""
    page_dir = PAGES_DIR / page_id
    if not page_dir.exists():
        abort(404)
    return send_from_directory(page_dir, filename)


@app.route("/page/<page_id>/render/<filename>")
def serve_render(page_id, filename):
    """Serve rendered output (SVG, PDF, HTML, V3D)."""
    page_dir = PAGES_DIR / page_id / "render"
    if not page_dir.exists():
        abort(404)
    return send_from_directory(page_dir, filename)


@app.route("/new", methods=["GET", "POST"])
def new_page():
    """Create a new example page."""
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        author = request.form.get("author", "").strip()
        email = request.form.get("email", "").strip()
        tags = request.form.get("tags", "").strip()
        md_text = request.form.get("page_md", "").strip()

        if not title:
            flash("Title is required.", "error")
            return render_template("edit.html", page=None), 400

        # Collect uploaded files
        uploaded = request.files.getlist("files")
        files = {}
        for f in uploaded:
            if f.filename:
                data = f.read()
                if len(data) > MAX_SOURCE_SIZE:
                    flash(f"File {f.filename} exceeds max size.", "error")
                    return render_template("edit.html", page=None), 400
                files[f.filename] = data

        # If no upload, check for pasted code
        if not files:
            code = request.form.get("code", "").strip()
            if code:
                files["main.asy"] = code.encode()

        if not files:
            flash("At least one .asy file or code is required.", "error")
            return render_template("edit.html", page=None), 400

        page_id = next_id(PAGES_DIR)
        page_dir = PAGES_DIR / page_id
        page_dir.mkdir(parents=True)
        (page_dir / "render").mkdir()

        # Write files
        for name, data in files.items():
            (page_dir / name).write_bytes(data)

        # Write page.md (use provided markdown or generate a default)
        if not md_text:
            main_file = next((n for n in files if n.endswith(".asy")), "main.asy")
            md_text = f"# {title}\n\n```asy:{main_file}\n```\n"
        (page_dir / "page.md").write_text(md_text)

        # Write meta.json
        meta = {
            "id": page_id,
            "title": title,
            "author": author,
            "email": email,
            "tags": [t.strip() for t in tags.split(",") if t.strip()],
            "created": datetime.now(timezone.utc).isoformat(),
            "modified": datetime.now(timezone.utc).isoformat(),
        }
        (page_dir / "meta.json").write_text(json.dumps(meta, indent=2))

        # Render all .asy files (SVG by default)
        for name in files:
            if name.endswith(".asy"):
                try:
                    render_asy(
                        src_file=page_dir / name,
                        out_file=page_dir / "render" / f"{name}.svg",
                        fmt="svg",
                        timeout=RENDER_TIMEOUT,
                        asy_binary=ASY_BINARY,
                        http_url=ASY_HTTP_URL,
                    )
                except Exception as e:
                    log.warning("Render failed for %s/%s: %s", page_id, name, e)

        log.info("Created page %s: %s", page_id, title)
        return redirect(url_for("view_page", page_id=page_id))

    return render_template("edit.html", page=None)


@app.route("/page/<page_id>/edit", methods=["GET", "POST"])
def edit_page(page_id):
    """Edit an existing page (MVP: replace files and re-render)."""
    page = get_page(PAGES_DIR, page_id)
    if page is None:
        abort(404)

    if request.method == "POST":
        page_dir = PAGES_DIR / page_id

        # Update metadata
        title = request.form.get("title", page["title"]).strip()
        author = request.form.get("author", page.get("author", "")).strip()
        tags = request.form.get("tags", ",".join(page.get("tags", []))).strip()
        md_text = request.form.get("page_md", "").strip()

        # Replace uploaded files
        uploaded = request.files.getlist("files")
        for f in uploaded:
            if f.filename:
                data = f.read()
                if len(data) > MAX_SOURCE_SIZE:
                    flash(f"File {f.filename} exceeds max size.", "error")
                    return render_template("edit.html", page=page), 400
                (page_dir / f.filename).write_bytes(data)

        # Remove old renders and re-render
        render_dir = page_dir / "render"
        if render_dir.exists():
            shutil.rmtree(render_dir)
        render_dir.mkdir()

        if md_text:
            (page_dir / "page.md").write_text(md_text)

        meta_file = page_dir / "meta.json"
        meta = json.loads(meta_file.read_text())
        meta["title"] = title
        meta["author"] = author
        meta["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        meta["modified"] = datetime.now(timezone.utc).isoformat()
        meta_file.write_text(json.dumps(meta, indent=2))

        for f in page_dir.iterdir():
            if f.suffix == ".asy":
                try:
                    render_asy(
                        src_file=f,
                        out_file=render_dir / f"{f.name}.svg",
                        fmt="svg",
                        timeout=RENDER_TIMEOUT,
                        asy_binary=ASY_BINARY,
                        http_url=ASY_HTTP_URL,
                    )
                except Exception as e:
                    log.warning("Re-render failed for %s: %s", f.name, e)

        return redirect(url_for("view_page", page_id=page_id))

    # GET: load current content for the form
    page_dir = PAGES_DIR / page_id
    md_text = (page_dir / "page.md").read_text() if (page_dir / "page.md").exists() else ""
    page["page_md"] = md_text
    return render_template("edit.html", page=page)


@app.route("/page/<page_id>/delete", methods=["POST"])
def delete_page_route(page_id):
    """Delete a page."""
    if delete_page(PAGES_DIR, page_id):
        flash("Page deleted.", "success")
    else:
        flash("Page not found.", "error")
    return redirect(url_for("index"))


# --- Error handlers ----------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


# --- Main --------------------------------------------------------------------

if __name__ == "__main__":
    host = os.environ.get("WIKI_HOST", "127.0.0.1")
    port = int(os.environ.get("WIKI_PORT", "8080"))
    log.info("Starting asymwiki on %s:%d (data: %s)", host, port, DATA_DIR)
    log.info("Renderer: %s", ASY_HTTP_URL or f"binary {ASY_BINARY}")
    app.run(host=host, port=port, debug=os.environ.get("FLASK_DEBUG") == "1")

