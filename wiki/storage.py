""
File-based storage for wiki pages.

Each page is a directory under PAGES_DIR with a zero-padded numeric name:
    0001-example-name/
        meta.json
        page.md
        *.asy
        render/
"""

import json
import shutil
from pathlib import Path
from typing import Optional


def list_pages(pages_dir: Path) -> list[dict]:
    """
    Return a list of page metadata dicts, sorted by ID (newest last).
    Each dict contains the contents of meta.json plus a 'thumbnail' key
    (path to the first SVG render, if available).
    """
    pages = []
    if not pages_dir.exists():
        return pages

    for d in sorted(pages_dir.iterdir()):
        if not d.is_dir():
            continue
        meta_file = d / "meta.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        # Find a thumbnail (first .html in output/)
        output_dir = d / "output"
        thumbnail = None
        if output_dir.exists():
            htmls = sorted(output_dir.glob("*.html"))
            if htmls:
                thumbnail = f"/page/{d.name}/output/{htmls[0].name}"
        meta["thumbnail"] = thumbnail
        meta["id"] = d.name
        pages.append(meta)

    return pages


def get_page(pages_dir: Path, page_id: str) -> Optional[dict]:
    """Return the meta.json for a page, or None if not found."""
    meta_file = pages_dir / page_id / "meta.json"
    if not meta_file.exists():
        return None
    try:
        return json.loads(meta_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save_page(pages_dir: Path, page_id: str, meta: dict) -> None:
    """Write meta.json for a page."""
    page_dir = pages_dir / page_id
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "meta.json").write_text(json.dumps(meta, indent=2))


def delete_page(pages_dir: Path, page_id: str) -> bool:
    """Delete a page directory. Returns True if it existed."""
    page_dir = pages_dir / page_id
    if page_dir.exists() and page_dir.is_dir():
        # Safety: only delete directories that have a meta.json
        if (page_dir / "meta.json").exists():
            shutil.rmtree(page_dir)
            return True
    return False


def next_id(pages_dir: Path) -> str:
    """
    Generate the next zero-padded page ID (0001, 0002, ...).
    Includes a slug from the title if provided via the module-level _slug.
    For simplicity, just use numeric: 0001, 0002, ...
    """
    if not pages_dir.exists():
        return "0001"

    max_n = 0
    for d in pages_dir.iterdir():
        if d.is_dir() and d.name[:4].isdigit():
            try:
                n = int(d.name[:4])
                max_n = max(max_n, n)
            except ValueError:
                pass

    return f"{max_n + 1:04d}
