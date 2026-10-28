"""Exercise release routing scripts without logging in or publishing artifacts."""

import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest
import yaml

from scripts.check_distributions import PACKAGE, REQUIRED_FILES, check_distributions

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_EXECUTABLE") or (
    shutil.which("bash") if sys.platform != "win32" else None
)
requires_bash = pytest.mark.skipif(
    not BASH, reason="Set BASH_EXECUTABLE to run Bash workflow tests"
)


def run_step(tmp_path, workflow, name, package_version="1.1.4", **environment):
    document = yaml.safe_load(
        (ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
    )
    steps = [step for job in document["jobs"].values() for step in job.get("steps", [])]
    script = next(step["run"] for step in steps if step.get("name") == name)
    output = tmp_path / "output"
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nversion = "{package_version}"\n', encoding="utf-8"
    )
    result = subprocess.run(
        [BASH, "--noprofile", "--norc", "-e", "-o", "pipefail"],
        input='export PATH="/usr/bin:$PATH"\n' + script,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
            "GITHUB_OUTPUT": output.as_posix(),
            **environment,
        },
    )
    return result, output.read_text(encoding="utf-8") if output.exists() else ""


@requires_bash
@pytest.mark.parametrize(
    "event,ref,requested,expected",
    [
        ("push", "v1.1.4", "", "pypi"),
        ("push", "v1.1.4alpha1", "", "testpypi"),
        ("workflow_dispatch", "v1.1.4alpha1", "pypi", "pypi"),
        ("workflow_dispatch", "v1.1.4", "testpypi", "testpypi"),
        ("workflow_dispatch", "main", "pypi", "pypi"),
    ],
)
def test_pypi_target_selection(tmp_path, event, ref, requested, expected):
    result, output = run_step(
        tmp_path,
        "PyPI-publish.yml",
        "Select publish target",
        GITHUB_EVENT_NAME=event,
        GITHUB_REF_NAME=ref,
        REQUESTED_TARGET=requested,
    )
    assert result.returncode == 0, result.stderr
    assert output.strip() == f"repository={expected}"


@requires_bash
@pytest.mark.parametrize("registry", ["", "dockerhub", "aliyun", "both"])
@pytest.mark.parametrize(
    "ref,version,latest",
    [
        ("refs/tags/v1.1.4", "1.1.4", True),
        ("refs/tags/docker-v1.1.4", "1.1.4", True),
        ("refs/tags/v1.1.4alpha1", "1.1.4alpha1", False),
        ("refs/heads/develop", "develop", False),
        ("refs/heads/main", "latest", False),
    ],
)
def test_docker_registry_and_tags(tmp_path, registry, ref, version, latest):
    result, output = run_step(
        tmp_path,
        "docker-publish.yml",
        "Get image tags",
        package_version=version if ref.startswith("refs/tags/") else "1.1.4",
        GITHUB_REF=ref,
        GITHUB_REF_NAME=ref.rsplit("/", 1)[1],
        REQUESTED_REGISTRY=registry,
    )
    assert result.returncode == 0, result.stderr
    tags = (
        output.split("tags<<IMAGE_TAGS\n", 1)[1].split("IMAGE_TAGS", 1)[0].splitlines()
    )
    repositories = []
    if registry != "aliyun":
        repositories.append("kineviz/kineviz-database-proxy")
    if registry in ("aliyun", "both"):
        repositories.append(
            "registry.cn-hangzhou.aliyuncs.com/kineviz/kineviz-database-proxy"
        )
    expected = [
        f"{repo}:{tag}"
        for repo in repositories
        for tag in ([version, "latest"] if latest else [version])
    ]
    assert tags == expected


@requires_bash
@pytest.mark.parametrize("prefix", ["v", "docker-v"])
def test_docker_rejects_mismatched_version(tmp_path, prefix):
    result, output = run_step(
        tmp_path,
        "docker-publish.yml",
        "Get image tags",
        GITHUB_REF=f"refs/tags/{prefix}9.9.9",
        GITHUB_REF_NAME=f"{prefix}9.9.9",
        REQUESTED_REGISTRY="dockerhub",
    )
    assert result.returncode != 0
    assert not output


@requires_bash
@pytest.mark.parametrize("tag,success", [("v1.1.4", True), ("v9.9.9", False)])
def test_pypi_validates_tag_version(tmp_path, tag, success):
    result, _ = run_step(
        tmp_path,
        "PyPI-publish.yml",
        "Validate release version",
        GITHUB_REF_NAME=tag,
    )
    assert (result.returncode == 0) is success


def make_archives(tmp_path, missing=None):
    names = set(REQUIRED_FILES) | {
        f"{PACKAGE}/static/main.js",
        f"{PACKAGE}/static/main.css",
    }
    names.discard(missing)
    with zipfile.ZipFile(tmp_path / "package.whl", "w") as wheel:
        for name in names:
            wheel.writestr(name, "data")
    with tarfile.open(tmp_path / "package.tar.gz", "w:gz") as source:
        for name in names:
            member = tarfile.TarInfo(f"package-1.1.4/src/{name}")
            member.size = 4
            source.addfile(member, io.BytesIO(b"data"))


def test_distributions_contain_runtime_assets(tmp_path):
    make_archives(tmp_path)
    check_distributions(tmp_path)


@pytest.mark.parametrize(
    "missing",
    [*REQUIRED_FILES, f"{PACKAGE}/static/main.js", f"{PACKAGE}/static/main.css"],
)
@pytest.mark.parametrize("archive", ["package.whl", "package.tar.gz"])
def test_distributions_reject_missing_assets(tmp_path, missing, archive):
    make_archives(tmp_path)
    valid = tmp_path / archive
    make_archives(tmp_path, missing)
    # Keep the other archive valid so each format is checked independently.
    invalid = valid.read_bytes()
    make_archives(tmp_path)
    valid.write_bytes(invalid)
    with pytest.raises(ValueError, match="missing"):
        check_distributions(tmp_path)


@pytest.mark.parametrize("missing", ["package.whl", "package.tar.gz"])
def test_distributions_require_both_formats(tmp_path, missing):
    make_archives(tmp_path)
    (tmp_path / missing).unlink()
    with pytest.raises(ValueError, match="both a wheel and a source"):
        check_distributions(tmp_path)
