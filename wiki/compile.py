
"""
Asymptote compilation: invoke the asy binary with the page directory as CWD.

This handles multi-file examples naturally: if main.asy does
input("data.txt") or import "helper", the files are found in the
same directory.

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
) -> None:
    """
    Compile an .asy file to the given output format.

    Parameters
    ----------
    src_file : Path to the .asy source file.
    out_file : Path where the compiled output should be written.
    fmt : Output format ("html" [default], "svg", "pdf", "v3d", "eps").
    timeout : Max seconds to allow the compilation.
    asy_binary : Path to the asy executable.
    extra_args : Additional arguments passed to asy.
    """
    src_file = Path(src_file)
    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if not src_file.exists():
        raise CompileError(f"Source file not found: {src_file}")

    cmd = [asy_binary, "-f", fmt, "-o", str(out_file), str(src_file)]
    if extra_args:
        cmd.extend(extra_args)

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

    # Verify output was produced. asy may name the file differently
    # depending on format, so glob for it if the exact path is missing.
    if not out_file.exists():
        candidates = list(out_file.parent.glob(f"{src_file.stem}.*"))
        # Filter out the source file itself and other .asy files
        candidates = [c for c in candidates if c.suffix != ".asy" and c != src_file]
        if candidates:
            candidates[0].rename(out_file)
        else:
            raise CompileError(
                f"Compilation completed but output not found: {out_file}"
            )

