"""The quickstart script refuses to run without Docker. The README is checked by test_readme.py."""

import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[4]


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
