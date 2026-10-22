"""
asymptote-wiki: A lightweight wiki for sharing Asymptote examples.

Entry point. Run with:
    python app.py

Configuration via environment variables:
    ASY_BINARY      Path to the asy executable (default: "asy")
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

from wiki.compile import compile_asy, CompileError
from wiki.storage import (
    list_pages, get_page, save_meta, delete_page, move_page,
    next_id, category_tree, search_pages
)
from wiki import gittrack

# --- Configuration ---------------------------------------------------------

DATA_DIR = Path(os.environ.get("WIKI_DATA_DIR", Path(__file__).parent / "data"))
PAGES_DIR = DATA_DIR / "pages"
PAGES_DIR.mkdir(parents=True, exist_ok=True)

# Ensure git tracking is active on the pages directory
gittrack.ensure_repo(PAGES_DIR)

ASY_BINARY = os.environ.get("ASY_BINARY", "asy")
COMPILE_TIMEOUT = int(os.environ.get("COMPILE_TIMEOUT", "30"))
MAX_SOURCE_SIZE = int(os.environ.get("MAX_SOURCE_SIZE", "100_000"))
MAX_CONCURRENT_COMPILES = int(os.environ.get("MAX_CONCURRENT_COMPILES", "3"))

import threading
_compile_sem = threading.Semaphore(MAX_CONCURRENT_COMPILES)

# --- App setup -------------------------------------------------------------

app = Flask(__name__)
WIKI_PREFIX = os.environ.get('WIKI_PREFIX', '')

# When deployed under /wiki/, rewrite URL prefixes in HTML responses.
# This is the most reliable approach -- no matter how Flask generates URLs,
# we fix them at the output level.
if WIKI_PREFIX:
    @app.after_request
    def _prefix_urls(response):
        # Prefix redirect Location headers
        loc = response.headers.get('Location', '')
        if loc.startswith('/') and not loc.startswith(WIKI_PREFIX):
            response.headers['Location'] = WIKI_PREFIX + loc
        # Prefix URLs in HTML bodies
        if response.content_type and 'text/html' in response.content_type:
            try:
                html = response.get_data(as_text=True)
                for attr in ('href', 'src', 'action'):
                    html = html.replace(f'{attr}="/', f'{attr}="{WIKI_PREFIX}/')
                    html = html.replace(f"{attr}='/", f"{attr}='{WIKI_PREFIX}/")
                html = html.replace(f'{WIKI_PREFIX}{WIKI_PREFIX}/', f'{WIKI_PREFIX}/')
                response.set_data(html)
            except RuntimeError:
                pass
        return response

app.secret_key = os.environ.get("WIKI_SECRET", "dev-change-me")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB upload limit

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("asywiki")

# --- Security ---------------------------------------------------------------

from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

csrf = CSRFProtect(app)
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200/hour"],
    storage_uri="memory://",
)

# Forbid debug mode in production (deployed under /wiki/ or with WIKI_HOST=0.0.0.0)
if os.environ.get("WIKI_PREFIX") or os.environ.get("WIKI_HOST") == "0.0.0.0":
    if os.environ.get("FLASK_DEBUG") == "1":
        raise RuntimeError("FLASK_DEBUG must not be set in production")

# --- Helpers ----------------------------------------------------------------

def _safe_page_dir(page_rel: str) -> Path:
    """Resolve a page relative path and ensure it stays within PAGES_DIR."""
    page_dir = (PAGES_DIR / page_rel).resolve()
    if not page_dir.is_relative_to(PAGES_DIR.resolve()):
        abort(400, description="Invalid path")
    return page_dir


ASY_FENCE_RE = re.compile(
    r"```asy:(?P<file>[\w.\-]+)(?:\?f=(?P<fmt>\w+))?\s*\n(?P<code>.*?)```",
    re.DOTALL
)


def _auto_display(page_dir: Path) -> str:
    """
    Auto-generate display HTML for all .asy files that have compiled output.
    Used when page.md doesn't contain explicit ```asy: fences.
    Modules (no output) are skipped automatically.
    """
    output_dir = page_dir / "output"
    parts = []
    if not output_dir.exists():
        return ""

    for html_file in sorted(output_dir.glob("*.html")):
        # html_file is e.g. output/test.asy.html -> source is test.asy
        src_name = html_file.name.removesuffix(".html")
        src_file = page_dir / src_name

        url = url_for("serve_output", page_rel=str(page_dir.relative_to(PAGES_DIR)), filename=html_file.name)
        display = (
            f"<div class='asy-output asy-html'>"
            f"<h3>{src_name}</h3>"
            f"<iframe src='{url}' frameborder='0' "
            f"style='width:100%;min-height:300px;border:1px solid #e5e7eb;"
            f"border-radius:6px;'></iframe>"
            f"<div class='asy-output-bar'>"
            f"<a href='{url}' target='_blank'>Open fullscreen</a>"
            f"</div></div>"
        )

        # Collapsible source
        if src_file.exists():
            code = src_file.read_text(errors="replace")
            escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            display += (
                f"<details class='asy-source'>"
                f"<summary>View source ({src_name})</summary>"
                f"<pre>{escaped}</pre></details>"
            )

        parts.append(display)

    return "\n".join(parts)


def compile_markdown_with_asy(md_text: str, page_dir: Path) -> str:
    """
    Process markdown, replacing ```asy:filename fences with the compiled output.
    The actual .asy source files live in page_dir; the fence content is
    displayed as collapsible source code.

    Default output is "html" (SVG for 2D, WebGL for 3D), displayed in an
    iframe -- the same approach as the Asymptote Web Application.
    """
    def replace_fence(m):
        filename = m.group("file")
        fmt = m.group("fmt") or "html"
        code = m.group("code")
        src_file = page_dir / filename

        # Skip if source file doesn't exist (e.g. referenced but not uploaded)
        if not src_file.exists():
            return ""

        # Determine output path
        out_file = page_dir / "output" / f"{filename}.{fmt}"
        if not out_file.exists():
            try:
                with _compile_sem:
                    compile_asy(
                        src_file=src_file,
                        out_file=out_file,
                        fmt=fmt,
                        timeout=COMPILE_TIMEOUT,
                        asy_binary=ASY_BINARY,
                        extra_args=["-noglobalread"],
                    )
            except CompileError as e:
                return f"<pre class='compile-error'>Compile failed for {filename}:\n{e}</pre>"
            except Exception as e:
                return f"<pre class='compile-error'>Error compiling {filename}:\n{e}</pre>"

        # If no output was produced (e.g. a module with no shipout()), skip it
        if not out_file.exists():
            return ""

        # Build the display: iframe for html, inline for svg, link for others
        if fmt == "html":
            url = url_for("serve_output", page_rel=str(page_dir.relative_to(PAGES_DIR)), filename=f"{filename}.html")
            display_html = (
                f"<div class='asy-output asy-html'>"
                f"<iframe src='{url}' frameborder='0' "
                f"style='width:100%;min-height:300px;border:1px solid #e5e7eb;"
                f"border-radius:6px;'></iframe>"
                f"<div class='asy-output-bar'>"
                f"<a href='{url}' target='_blank'>Open fullscreen</a>"
                f"</div></div>"
            )
        elif fmt == "svg":
            svg_content = out_file.read_text()
            display_html = f"<div class='asy-output asy-svg'>{svg_content}</div>"
        elif fmt == "pdf":
            url = url_for("serve_output", page_rel=str(page_dir.relative_to(PAGES_DIR)), filename=f"{filename}.pdf")
            display_html = (
                f"<div class='asy-output asy-pdf'>"
                f"<iframe src='{url}' style='width:100%;height:400px;"
                f"border:1px solid #e5e7eb;border-radius:6px;'></iframe></div>"
            )
        else:
            url = url_for("serve_output", page_rel=str(page_dir.relative_to(PAGES_DIR)), filename=f"{filename}.{fmt}")
            display_html = f"<p><a href='{url}'>Download {filename}.{fmt}</a></p>"

        # Collapsible source
        escaped_code = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        src_html = (
            f"<details class='asy-source'>"
            f"<summary>View source ({filename})</summary>"
            f"<pre>{escaped_code}</pre></details>"
        )

        return display_html + src_html

    # Replace asy fences with compiled output
    html_with_asy = ASY_FENCE_RE.sub(replace_fence, md_text)

    # Process the rest as markdown
    rendered = markdown.markdown(html_with_asy, extensions=["fenced_code", "tables"])
    return rendered


# --- Routes -----------------------------------------------------------------

@app.route("/")
def index():
    """Gallery listing of all pages, optionally filtered by category."""
    cat_filter = request.args.get("cat", "")
    pages = list_pages(PAGES_DIR, category=cat_filter)
    tree = category_tree(PAGES_DIR)
    return render_template("index.html", pages=pages, tree=tree,
                           current_cat=cat_filter)


@app.route("/search")
def search():
    """Search pages by title, author, tags, or source content."""
    query = request.args.get("q", "").strip()
    pages = search_pages(PAGES_DIR, query) if query else []
    tree = category_tree(PAGES_DIR)
    return render_template("index.html", pages=pages, tree=tree,
                           current_cat="", search_query=query)


@app.route("/page/<path:page_rel>")
def view_page(page_rel):
    """View a single example page. page_rel is like '3D/Surfaces/0001' or '0004'."""
    page = get_page(PAGES_DIR, page_rel)
    if page is None:
        abort(404)

    page_dir = _safe_page_dir(page_rel)
    md_file = page_dir / "page.md"
    md_text = md_file.read_text() if md_file.exists() else ""

    # If page.md has explicit asy fences, use them.
    # Otherwise, auto-display all .asy files that produced output.
    if ASY_FENCE_RE.search(md_text):
        content_html = compile_markdown_with_asy(md_text, page_dir)
    else:
        content_html = _auto_display(page_dir)
        # Still render any surrounding markdown prose
        if md_text.strip():
            prose_html = markdown.markdown(md_text, extensions=["fenced_code", "tables"])
            content_html = prose_html + content_html

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
        page_rel=page_rel,
    )


@app.route("/page/<path:page_rel>/raw/<filename>")
def serve_source(page_rel, filename):
    """Serve raw source file."""
    page_dir = _safe_page_dir(page_rel)
    if not page_dir.exists():
        abort(404)
    return send_from_directory(page_dir, filename)


@app.route("/page/<path:page_rel>/output/<filename>")
def serve_output(page_rel, filename):
    """Serve compiled output (HTML, SVG, PDF, V3D)."""
    page_dir = _safe_page_dir(page_rel) / "output"
    if not page_dir.exists():
        abort(404)
    return send_from_directory(page_dir, filename)


@app.route("/new", methods=["GET", "POST"])
def new_page():
    """Create a new example page."""
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        author = request.form.get("author", "").strip()
        tags = request.form.get("tags", "").strip()
        category = request.form.get("category", "").strip().strip("/")
        md_text = request.form.get("page_md", "").strip()

        if not title:
            flash("Title is required.", "error")
            return render_template("edit.html", page=None, existing_files=[]), 400

        # Collect uploaded files
        uploaded = request.files.getlist("files")
        files = {}
        for f in uploaded:
            if f.filename:
                data = f.read()
                if len(data) > MAX_SOURCE_SIZE:
                    flash(f"File {f.filename} exceeds max size.", "error")
                    return render_template("edit.html", page=None, existing_files=[]), 400
                files[f.filename] = data

        # If no upload, check for pasted code
        if not files:
            code = request.form.get("code", "").strip()
            if code:
                files["main.asy"] = code.encode()

        if not files:
            flash("At least one .asy file or code is required.", "error")
            return render_template("edit.html", page=None, existing_files=[]), 400



        page_id = next_id(PAGES_DIR, title)
        # Atomically claim the directory to prevent duplicates from
        # double-submits. mkdir fails if the path already exists.
        base_slug = page_id
        page_dir = PAGES_DIR / category / page_id if category else PAGES_DIR / page_id
        n = 2
        while True:
            try:
                page_dir.parent.mkdir(parents=True, exist_ok=True)
                page_dir.mkdir()  # raises FileExistsError if taken
                break
            except FileExistsError:
                page_id = f"{base_slug}-{n}"
                page_dir = PAGES_DIR / category / page_id if category else PAGES_DIR / page_id
                n += 1
        (page_dir / "output").mkdir()

        # Write files
        for name, data in files.items():
            (page_dir / name).write_bytes(data)

        # Write page.md (user's description, may be empty)
        (page_dir / "page.md").write_text(md_text)

        # Write meta.json
        meta = {
            "id": page_id,
            "title": title,
            "author": author,
            "tags": [t.strip() for t in tags.split(",") if t.strip()],
            "created": datetime.now(timezone.utc).isoformat(),
            "modified": datetime.now(timezone.utc).isoformat(),
        }
        (page_dir / "meta.json").write_text(json.dumps(meta, indent=2))

        # Compile all .asy files (modules that produce no output are skipped)
        _recompile_page(page_dir)

        page_rel = str(page_dir.relative_to(PAGES_DIR))
        log.info("Created page %s: %s", page_rel, title)

        # Git tracking
        gittrack.commit_all(f"Add: {title}", PAGES_DIR)
        gittrack.push(PAGES_DIR)

        return redirect(url_for("view_page", page_rel=page_rel))

    return render_template("edit.html", page=None, existing_files=[])


@app.route("/page/<path:page_rel>/edit", methods=["GET", "POST"])
def edit_page(page_rel):
    """Edit an existing page (MVP: replace files and re-render)."""
    page = get_page(PAGES_DIR, page_rel)
    if page is None:
        abort(404)

    if request.method == "POST":
        page_dir = _safe_page_dir(page_rel)

        # Update metadata
        title = request.form.get("title", page["title"]).strip()
        author = request.form.get("author", page.get("author", "")).strip()
        tags = request.form.get("tags", ",".join(page.get("tags", []))).strip()
        new_category = request.form.get("category", "").strip().strip("/")
        md_text = request.form.get("page_md", "").strip()

        # Move page if category changed
        old_category = page.get("category", "")
        if new_category != old_category:
            moved = move_page(PAGES_DIR, page_rel, new_category)
            if moved:
                page_rel = str((PAGES_DIR / new_category / page_dir.name).relative_to(PAGES_DIR)) if new_category else page_dir.name
                page_dir = PAGES_DIR / page_rel
                flash(f"Moved to {new_category or 'root'}.", "success")
            else:
                flash("Could not move (destination exists?).", "error")
                return redirect(url_for("view_page", page_rel=page_rel))

        # Replace uploaded files
        uploaded = request.files.getlist("files")
        for f in uploaded:
            if f.filename:
                data = f.read()
                if len(data) > MAX_SOURCE_SIZE:
                    flash(f"File {f.filename} exceeds max size.", "error")
                    return render_template("edit.html", page=page), 400
                (page_dir / f.filename).write_bytes(data)

        (page_dir / "page.md").write_text(md_text)

        meta_file = page_dir / "meta.json"
        meta = json.loads(meta_file.read_text())
        meta["title"] = title
        meta["author"] = author
        meta["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        meta["modified"] = datetime.now(timezone.utc).isoformat()
        meta_file.write_text(json.dumps(meta, indent=2))

        # Recompile all .asy files
        _recompile_page(page_dir)

        # Git tracking
        gittrack.commit_all(f"Edit: {title}", PAGES_DIR)
        gittrack.push(PAGES_DIR)

        return redirect(url_for("view_page", page_rel=page_rel))

    # GET: load current content for the form
    page_dir = _safe_page_dir(page_rel)
    md_text = (page_dir / "page.md").read_text() if (page_dir / "page.md").exists() else ""
    page["page_md"] = md_text
    existing_files = [
        f.name for f in sorted(page_dir.iterdir())
        if f.is_file() and f.suffix in (".asy", ".txt", ".csv", ".dat")
        and f.parent == page_dir
    ]
    return render_template("edit.html", page=page, existing_files=existing_files)


@app.route("/page/<path:page_rel>/delete", methods=["POST"])
def delete_page_route(page_rel):
    """Delete a page."""
    page = get_page(PAGES_DIR, page_rel)
    if page:
        if delete_page(PAGES_DIR, page_rel):
            flash("Page deleted.", "success")
            # Git tracking
            gittrack.commit_all(f"Delete: {page['title']}", PAGES_DIR)
            gittrack.push(PAGES_DIR)
        else:
            flash("Page not found.", "error")
    else:
        flash("Page not found.", "error")
    return redirect(url_for("index"))


# --- File management (add/replace/delete individual files) --------------------

@app.route("/page/<path:page_rel>/files", methods=["POST"])
def add_file(page_rel):
    """Add or replace a file on an existing page. Recompile afterwards."""
    page = get_page(PAGES_DIR, page_rel)
    if page is None:
        abort(404)

    page_dir = _safe_page_dir(page_rel)
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        flash("No file uploaded.", "error")
        return redirect(url_for("view_page", page_rel=page_rel))

    data = uploaded.read()
    if len(data) > MAX_SOURCE_SIZE:
        flash(f"File {uploaded.filename} exceeds max size.", "error")
        return redirect(url_for("view_page", page_rel=page_rel))

    # Sanitize filename (no path traversal)
    safe_name = Path(uploaded.filename).name
    (page_dir / safe_name).write_bytes(data)
    flash(f"Saved {safe_name}.", "success")

    # Recompile if it's an .asy file
    if safe_name.endswith(".asy"):
        _recompile_page(page_dir)

    # Git tracking
    gittrack.commit_all(f"Add file: {safe_name}", PAGES_DIR)
    gittrack.push(PAGES_DIR)

    return redirect(url_for("view_page", page_rel=page_rel))


@app.route("/page/<path:page_rel>/files/<filename>/delete", methods=["POST"])
def remove_file(page_rel, filename):
    """Delete a specific file from a page."""
    page = get_page(PAGES_DIR, page_rel)
    if page is None:
        abort(404)

    page_dir = _safe_page_dir(page_rel)
    safe_name = Path(filename).name  # prevent path traversal
    target = page_dir / safe_name

    if target.exists() and target.is_file() and safe_name not in ("page.md", "meta.json"):
        target.unlink()
        # Also remove its compiled output if present
        output_dir = page_dir / "output"
        for fmt in ("html", "svg", "pdf", "v3d", "eps"):
            out = output_dir / f"{safe_name}.{fmt}"
            if out.exists():
                out.unlink()
        flash(f"Deleted {safe_name}.", "success")

        # Git tracking
        gittrack.commit_all(f"Remove file: {safe_name}", PAGES_DIR)
        gittrack.push(PAGES_DIR)
    else:
        flash(f"File {safe_name} not found.", "error")

    return redirect(url_for("view_page", page_rel=page_rel))


def _recompile_page(page_dir: Path) -> None:
    """Clear output dir and recompile all .asy files in the page."""
    output_dir = page_dir / "output"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir()

    for f in sorted(page_dir.iterdir()):
        if f.suffix == ".asy" and f.parent == page_dir:
            try:
                with _compile_sem:
                    compile_asy(
                        src_file=f,
                        out_file=output_dir / f"{f.name}.html",
                        fmt="html",
                        timeout=COMPILE_TIMEOUT,
                        asy_binary=ASY_BINARY,
                        extra_args=["-noglobalread"],
                    )
            except Exception as e:
                log.warning("Compile failed for %s: %s", f.name, e)


# Exempt file-serving routes from rate limiting (iframes load these frequently)
limiter.exempt(serve_output)
limiter.exempt(serve_source)


# --- Error handlers ----------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


# --- Main --------------------------------------------------------------------

if __name__ == "__main__":
    host = os.environ.get("WIKI_HOST", "127.0.0.1")
    port = int(os.environ.get("WIKI_PORT", "8080"))
    log.info(
        "Starting asywiki on %s:%d (data: %s, asy: %s, timeout: %ds)",
        host, port, DATA_DIR, ASY_BINARY, COMPILE_TIMEOUT,
    )
    app.run(host=host, port=port, debug=os.environ.get("FLASK_DEBUG") == "1")

