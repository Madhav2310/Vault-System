"""S16.06: README skeleton and quickstart script."""

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[4]
HEADINGS = [
    "Quickstart",
    "What works today",
    "Architecture",
    "Key design decisions",
    "Evaluation",
    "Scale",
    "Limitations and what was deliberately not built",
    "Repo map",
]


def test_headings() -> None:
    text = (ROOT / "README.md").read_text()
    assert text.startswith("# Vault")  # heading 1: what it is
    found = re.findall(r"^## (.+)$", text, re.M)
    assert found == HEADINGS


def test_quickstart_docker_check(tmp_path: pathlib.Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nexit 1\n")
    docker.chmod(0o755)
    r = subprocess.run(
        ["bash", str(ROOT / "scripts/quickstart.sh")],
        env={"PATH": f"{fake_bin}:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0 and "Docker is not running" in r.stderr
