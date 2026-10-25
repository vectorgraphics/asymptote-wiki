"""
File-based storage for wiki pages.

Pages are organized in a directory tree under pages_dir. The directory
path *is* the category:

    pages/
     3D/
        Surfaces/
           0001/
              meta.json
              page.md
              main.asy
              output/
        Curves/
            0002/
     2D/
        0003/
     0004/          (unfiled -- no category)

A "page" is any directory containing a meta.json.
A "category" is any directory containing subdirectories.
"""

import json
import shutil
from pathlib import Path
from typing import Optional


def list_pages(pages_dir: Path, category: str = "") -> list[dict]:
    """
    Return a list of page metadata dicts.

    If category is empty, list all pages (recursively).
    If category is given (e.g. "3D/Surfaces"), list only pages in that
    directory and its subdirectories.

    Each dict contains meta.json contents plus:
      - "id": the page directory name (e.g. "0001")
      - "category": relative path from pages_dir (e.g. "3D/Surfaces")
      - "thumbnail": path to first output HTML (or None)
    """
    pages = []
    search_dir = pages_dir / category if category else pages_dir
    if not search_dir.exists():
        return pages

    # List pages directly in this directory (not recursive).
    # Use rglob only when a specific category is requested (to include
    # subcategory pages when browsing a parent category).
    if category:
        meta_files = sorted(search_dir.rglob("meta.json"))
    else:
        meta_files = sorted(search_dir.glob("*/meta.json"))

    for meta_file in meta_files:
        page_dir = meta_file.parent
        try:
            meta = json.loads(meta_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        # Category is the relative path of page_dir from pages_dir,
        # excluding the page ID itself.
        rel = page_dir.relative_to(pages_dir)
        parts = rel.parts
        meta["category"] = "/".join(parts[:-1]) if len(parts) > 1 else ""
        meta["id"] = page_dir.name
        meta["rel_path"] = str(rel)

        # Thumbnail: first .html in output/
        output_dir = page_dir / "output"
        thumbnail = None
        if output_dir.exists():
            htmls = sorted(output_dir.glob("*.html"))
            if htmls:
                thumbnail = f"/page/{meta['rel_path']}/output/{htmls[0].name}"
        meta["thumbnail"] = thumbnail
        pages.append(meta)

    return pages


def category_tree(pages_dir: Path) -> list[dict]:
    """
    Build the category tree from the directory structure.
    Returns a list of dicts: {"name": ..., "path": ..., "children": [...]}
    Only directories that contain pages (directly or nested) are included.
    """
    def walk(d: Path, current_path: str) -> list[dict]:
        entries = []
        if not d.exists():
            return entries
        for sub in sorted(d.iterdir()):
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            sub_path = f"{current_path}/{sub.name}" if current_path else sub.name
            # A directory is a category only if it contains subdirectories
            # that are themselves pages or categories. A bare page directory
            # (containing meta.json directly) is NOT a category.
            # If this dir IS a page (has meta.json), it's not a category
            if (sub / "meta.json").exists():
                continue
            subdirs = [f for f in sub.iterdir() if f.is_dir() and not f.name.startswith(".")]
            # A category must contain at least one page (directly or nested)
            if not any(f.rglob("meta.json") for f in subdirs):
                continue
            children = walk(sub, sub_path)
            entry = {"name": sub.name, "path": sub_path}
            if children:
                entry["children"] = children
            entries.append(entry)
        return entries

    return walk(pages_dir, "")


def get_page(pages_dir: Path, rel_path: str) -> Optional[dict]:
    """Return meta.json for a page given its relative path (e.g. '3D/Surfaces/0001')."""
    meta_file = pages_dir / rel_path / "meta.json"
    if not meta_file.exists():
        return None
    try:
        meta = json.loads(meta_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    meta["rel_path"] = rel_path
    parts = Path(rel_path).parts
    meta["category"] = "/".join(parts[:-1]) if len(parts) > 1 else ""
    return meta


def save_meta(pages_dir: Path, rel_path: str, meta: dict) -> None:
    """Write meta.json for a page."""
    page_dir = pages_dir / rel_path
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "meta.json").write_text(json.dumps(meta, indent=2))


def delete_page(pages_dir: Path, rel_path: str) -> bool:
    """Delete a page directory. Returns True if it existed."""
    page_dir = pages_dir / rel_path
    if page_dir.exists() and page_dir.is_dir():
        if (page_dir / "meta.json").exists():
            shutil.rmtree(page_dir)
            # Clean up empty parent directories (but not pages_dir itself)
            parent = page_dir.parent
            while parent != pages_dir and parent.exists() and not any(
                f.is_dir() and (f / "meta.json").exists() for f in parent.iterdir()
            ) and not any(
                f.is_dir() and any(f.rglob("meta.json")) for f in parent.iterdir()
            ):
                parent.rmdir()
                parent = parent.parent
            return True
    return False


def move_page(pages_dir: Path, old_rel: str, new_category: str) -> bool:
    """
    Move a page to a new category directory.
    old_rel: current relative path (e.g. "2D/0003")
    new_category: target category (e.g. "3D/Surfaces") or "" for root
    """
    old_dir = pages_dir / old_rel
    if not old_dir.exists() or not (old_dir / "meta.json").exists():
        return False

    page_id = old_dir.name
    new_dir = pages_dir / new_category / page_id if new_category else pages_dir / page_id
    # The destination must stay inside pages_dir (defense in depth; callers
    # also validate the category format).
    if not new_dir.resolve().is_relative_to(pages_dir.resolve()):
        return False
    new_dir.parent.mkdir(parents=True, exist_ok=True)

    if new_dir.exists():
        return False  # conflict

    shutil.move(str(old_dir), str(new_dir))

    # Clean up empty old parent dirs
    parent = old_dir.parent
    while parent != pages_dir and parent.exists():
        has_content = any(
            f.is_dir() and any(f.rglob("meta.json")) for f in parent.iterdir()
        )
        if not has_content:
            parent.rmdir()
            parent = parent.parent
        else:
            break

    return True


def next_id(parent_dir: Path, title: str = "") -> str:
    """Generate a page directory name from the title.
    Uses the title as-is (stripped of path-unsafe chars).
    If the name is already taken in the same parent directory,
    append -2, -3, etc.
    """
    import re
    if title:
        title = re.sub(r"\.asy$", "", title)  # strip extension if present
        slug = re.sub(r'[/\\:\0]', "", title).strip()
    else:
        slug = "untitled"
    if not slug:
        slug = "untitled"
    base = slug
    n = 2
    while _name_exists_in(parent_dir, slug):
        slug = f"{base}-{n}"
        n += 1
    return slug


def _name_exists_in(parent_dir: Path, name: str) -> bool:
    """Check if a directory with this name exists in the given parent."""
    candidate = parent_dir / name
    return candidate.is_dir() and (candidate / "meta.json").exists()


def search_pages(pages_dir: Path, query: str) -> list[dict]:
    """
    Search pages by title, author, tags, and .asy source content.
    Returns a list of (meta_dict, match_fields) where match_fields is a
    list of strings describing where the match occurred (e.g. "title",
    "tag: parametric", "source: main.asy").
    """
    query_lower = query.lower().strip()
    if not query_lower:
        return []

    results = []
    if not pages_dir.exists():
        return results

    for meta_file in sorted(pages_dir.rglob("meta.json")):
        page_dir = meta_file.parent
        try:
            meta = json.loads(meta_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        rel = page_dir.relative_to(pages_dir)
        meta["id"] = page_dir.name
        meta["rel_path"] = str(rel)
        parts = rel.parts
        meta["category"] = "/".join(parts[:-1]) if len(parts) > 1 else ""

        # Thumbnail
        output_dir = page_dir / "output"
        thumbnail = None
        if output_dir.exists():
            htmls = sorted(output_dir.glob("*.html"))
            if htmls:
                thumbnail = f"/page/{meta['rel_path']}/output/{htmls[0].name}"
        meta["thumbnail"] = thumbnail

        match_fields = []

        # Check title
        title = meta.get("title", "")
        if query_lower in title.lower():
            match_fields.append("title")

        # Check author
        author = meta.get("author", "")
        if author and query_lower in author.lower():
            match_fields.append("author")

        # Check tags
        for tag in meta.get("tags", []):
            if query_lower in tag.lower():
                match_fields.append(f"tag: {tag}")

        # Check .asy source files
        for src in sorted(page_dir.iterdir()):
            if src.suffix == ".asy" and src.is_file():
                try:
                    content = src.read_text(errors="replace")
                    if query_lower in content.lower():
                        match_fields.append(f"source: {src.name}")
                except OSError:
                    pass

        if match_fields:
            meta["match_fields"] = match_fields
            results.append(meta)

    return results
