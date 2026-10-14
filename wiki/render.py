
"""
Asymptote rendering: either shell out to the asy binary or POST to
the asymptote-http-server.
"""

import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional


class RenderError(Exception):
    """Raised when Asymptote rendering fails."""
    pass


def render_asy(
    src_file: Path,
    out_file: Path,
    fmt: str = "svg",
    timeout: int = 30,
    asy_binary: str = "asy",
    http_url: str = "",
    render_scale: int = 2,
) -> None:
    """
    Render an .asy file to the given output format.

    If http_url is set, POST the source to the asymptote-http-server.
    Otherwise, invoke the asy binary directly.

    Parameters
    ----------
    src_file : Path to the .asy source file.
    out_file : Path where the rendered output should be written.
    fmt : Output format ("svg", "pdf", "html", "v3d", "eps", ...).
    timeout : Max seconds to allow the render.
    asy_binary : Path to the asy executable.
    http_url : Base URL of asymptote-http-server (e.g. "http://localhost:10007").
    render_scale : Pixels per bp for bitmap preview (pdf mode).
    """
    src_file = Path(src_file)
    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if not src_file.exists():
        raise RenderError(f"Source file not found: {src_file}")

    if http_url:
        _render_via_http(src_file, out_file, fmt, http_url, timeout, render_scale)
    else:
        _render_via_binary(src_file, out_file, fmt, asy_binary, timeout)


def _render_via_binary(
    src_file: Path,
    out_file: Path,
    fmt: str,
    asy_binary: str,
    timeout: int,
) -> None:
    """Render by invoking the asy executable."""
    # asy outputs to <stem>.<ext> by default; use -o for explicit path.
    # For some formats, asy wants just the stem (it appends the extension).
    cmd = [asy_binary, "-f", fmt, "-o", str(out_file)]

    # Run in the source file's directory so relative imports/data work.
    cwd = src_file.parent

    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RenderError(f"Render timed out after {timeout}s: {src_file.name}")
    except FileNotFoundError:
        raise RenderError(f"asy binary not found: {asy_binary}")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        # asy sometimes prints errors to stdout
        if not stderr and result.stdout.strip():
            stderr = result.stdout.strip()
        raise RenderError(f"asy exited with code {result.returncode}:\n{stderr}")

    # Verify the output was actually produced.
    # For some formats asy may write a differently-named file.
    if not out_file.exists():
        # Try stem-based name (asy sometimes ignores -o extension)
        alt = out_file.with_suffix("")
        candidates = list(out_file.parent.glob(f"{src_file.stem}.*"))
        if candidates:
            candidates[0].rename(out_file)
        else:
            raise RenderError(f"Render completed but output not found: {out_file}")


def _render_via_http(
    src_file: Path,
    out_file: Path,
    fmt: str,
    http_url: str,
    timeout: int,
    render_scale: int,
) -> None:
    """Render by POSTing source to the asymptote-http-server."""
    source = src_file.read_bytes()

    # Build query string
    params = [f"f={fmt}"]
    if fmt == "pdf":
        params.append(f"render={render_scale}")
    url = f"{http_url.rstrip('/')}?{'&'.join(params)}"

    req = urllib.request.Request(
        url,
        data=source,
        method="POST",
        headers={"Content-Type": "application/octet-stream"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:2000]
        raise RenderError(f"HTTP {e.code} from render server:\n{body}")
    except urllib.error.URLError as e:
        raise RenderError(f"Cannot reach render server at {http_url}: {e.reason}")
    except TimeoutError:
        raise RenderError(f"Render timed out after {timeout}s (HTTP): {src_file.name}")

    if not data:
        raise RenderError(f"Render server returned empty response for {src_file.name}")

    out_file.write_bytes(data)

