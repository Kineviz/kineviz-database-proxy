"""Upload prepared distributions without exposing credentials in diagnostics."""

from pathlib import Path
import os
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/upload_pypi.py DIST_DIRECTORY")
    directory = Path(sys.argv[1])
    distributions = sorted(directory.glob("*.whl")) + sorted(directory.glob("*.tar.gz"))
    if not distributions:
        raise SystemExit("No wheel or source distributions found")
    configured_token = os.environ.get("TWINE_PASSWORD", "")
    token = configured_token.strip()
    if not token:
        raise SystemExit("TWINE_PASSWORD is not configured")
    result = subprocess.run(
        [
            sys.executable, "-m", "twine", "upload", "--non-interactive",
            "--skip-existing", "--verbose", *(str(path) for path in distributions),
        ],
        env={**os.environ, "TWINE_USERNAME": "__token__", "TWINE_PASSWORD": token},
        capture_output=True,
        text=True,
    )
    for output in (result.stdout, result.stderr):
        print(output.replace(configured_token, "<redacted>").replace(token, "<redacted>"), end="")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
