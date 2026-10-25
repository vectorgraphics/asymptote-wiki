
"""
Asymptote compilation: invoke the asy binary with the page directory as CWD.

Compiles source files in place so that multi-file examples work naturally:
if main.asy does `import "helper"` or `input("data.txt")`, the files are
found alongside it. Output is directed to a target directory via -o.

The default output format is "html": asy produces a self-contained
HTML file with inline SVG for 2D or WebGL (asygl.js) for 3D,
matching the Asymptote Web Application.
"""

import subprocess
from pathlib import Path


class CompileError(Exception):
    """Raised when Asymptote compilation fails."""
    pass


def compile_asy(
    src_file: Path,
    out_file: Path,
    fmt: str = "html",
    timeout: int = 30,
    asy_binary: str = "asy",
    extra_args: list[str] | None = None,
    search_dir: str | None = None,
) -> None:
    """
    Compile an .asy file in place, directing output to the out_file's directory.

    Uses -o <output_dir>/ so asy preserves the source filename.
    The file is compiled from its original location so imports and
    data file references resolve naturally.

    Parameters
    ----------
    src_file : Path to the .asy source file.
    out_file : Expected output path (its parent dir is used as -o target).
    fmt : Output format ("html" [default], "svg", "pdf", "v3d", "eps").
    timeout : Max seconds to allow the compilation.
    asy_binary : Path to the asy executable.
    extra_args : Additional arguments passed to asy.
    """
    src_file = Path(src_file)
    out_file = Path(out_file)
    out_dir = out_file.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    if not src_file.exists():
        raise CompileError(f"Source file not found: {src_file}")

    # -o <dir>/  -> asy writes <dir>/<srcname>.<ext>
    cmd = [asy_binary, "-f", fmt, "-o", str(out_dir) + "/", src_file.name]
    if search_dir:
        cmd.extend(["-dir", search_dir])
    if extra_args:
        cmd.extend(extra_args)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(src_file.parent),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise CompileError(
            f"Compilation timed out after {timeout}s: {src_file.name}"
        )
    except FileNotFoundError:
        raise CompileError(
            f"asy binary not found: {asy_binary}. "
            f"Set ASY_BINARY to the correct path."
        )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if not stderr and result.stdout.strip():
            stderr = result.stdout.strip()
        raise CompileError(
            f"asy exited with code {result.returncode} ({src_file.name}):\n{stderr}"
        )

    # If output wasn't produced, this is likely a module (no shipout()).
    # That's fine -- the file is still available for imports.

