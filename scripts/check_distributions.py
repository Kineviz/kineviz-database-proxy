"""Check that release archives contain the UI and runtime package data."""

from pathlib import Path
import sys
import tarfile
import zipfile

PACKAGE = "kineviz_database_proxy"
REQUIRED_FILES = (
    f"{PACKAGE}/static/index.html",
    f"{PACKAGE}/contract/contract.schema.json",
    f"{PACKAGE}/drivers/relational/data/mapping.example.yml",
)


def check_files(names: set[str], archive: Path) -> None:
    """Require usable frontend assets and the data needed by API endpoints."""
    missing = [name for name in REQUIRED_FILES if name not in names]
    if missing:
        raise ValueError(f"{archive}: missing package data: {', '.join(missing)}")
    for extension in (".js", ".css"):
        if not any(
            name.startswith(f"{PACKAGE}/static/") and name.endswith(extension)
            for name in names
        ):
            raise ValueError(f"{archive}: missing frontend {extension} assets")


def check_distributions(directory: Path) -> None:
    """Check both the wheel and the source archive without extracting files."""
    wheels = sorted(directory.glob("*.whl"))
    sources = sorted(directory.glob("*.tar.gz"))
    if not wheels or not sources:
        raise ValueError(
            f"{directory}: both a wheel and a source distribution are required"
        )
    for archive in wheels:
        with zipfile.ZipFile(archive) as wheel:
            check_files(set(wheel.namelist()), archive)
        print(f"Checked {archive}")
    for archive in sources:
        with tarfile.open(archive) as source:
            # Source archives wrap the package tree in NAME-VERSION/src/.
            names = {
                name.split("/src/", 1)[1]
                for name in source.getnames()
                if "/src/" in name
            }
            check_files(names, archive)
        print(f"Checked {archive}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/check_distributions.py DIST_DIRECTORY")
    check_distributions(Path(sys.argv[1]))
