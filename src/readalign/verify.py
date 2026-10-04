"""Optional epubcheck pass, run only when ``--verify`` asks for it.

epubcheck is the W3C's reference validator and needs Java, which is too heavy a prerequisite
to put on the happy path, so nothing here runs unless the flag is passed. With it, the result
is checked the way the README tells users to check it by hand, and the outcome lands in the
report and the exit code instead of a command they have to remember.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess

from .errors import DependencyError

#: Where a jar is looked for when the flag is passed without a path.
_SEARCH_ENV = ["EPUBCHECK_HOME", "JAVA_HOME"]


def find_epubcheck(explicit: str | None) -> str:
    """Locate epubcheck: an explicit path, a wrapper on PATH, or a jar in the usual places."""
    if explicit and explicit != "auto":
        if not os.path.isfile(explicit):
            raise DependencyError(f"--verify points at a jar that is not there: {explicit}")
        return explicit
    wrapper = shutil.which("epubcheck")
    if wrapper:
        return wrapper
    roots = [os.environ.get(name, "") for name in _SEARCH_ENV]
    roots.extend(os.environ.get("PATH", "").split(os.pathsep))
    for root in roots:
        if not root:
            continue
        for jar in sorted(glob.glob(os.path.join(root, "epubcheck*.jar"))):
            return jar
    raise DependencyError(
        "--verify needs epubcheck, which needs Java. Get it from "
        "https://github.com/w3c/epubcheck, then either put epubcheck.jar somewhere on PATH "
        "(or in EPUBCHECK_HOME), or pass the path directly: --verify C:/tools/epubcheck.jar"
    )


def parse_epubcheck(output: str) -> tuple[int, int]:
    """Count the ERROR and WARNING lines epubcheck prints, whatever its exit code says."""
    errors = sum(1 for line in output.splitlines() if "ERROR(" in line)
    warnings = sum(1 for line in output.splitlines() if "WARNING(" in line)
    return errors, warnings


def run_epubcheck(epub: str, tool: str, *, log=print) -> dict:
    """Validate one EPUB, reporting rather than raising, and return what happened."""
    command = ["java", "-jar", tool, epub] if tool.endswith(".jar") else [tool, epub]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
    except OSError as exc:
        raise DependencyError(
            f"could not run epubcheck ({command[0]}). Is Java installed and on PATH? "
            f"Underlying error: {exc}"
        ) from exc
    output = (result.stdout or "") + (result.stderr or "")
    errors, warnings = parse_epubcheck(output)
    for line in [line for line in output.splitlines() if line.strip()][-3:]:
        log(f"  epubcheck     {line}")
    return {
        "tool": tool,
        "exit_code": result.returncode,
        "errors": errors,
        "warnings": warnings,
        "ok": result.returncode == 0 and errors == 0,
    }
