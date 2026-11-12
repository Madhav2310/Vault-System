import pathlib
import re

import yaml

COMPOSE = pathlib.Path(__file__).resolve().parents[4] / "docker-compose.yml"
PINNED = re.compile(r"^[\w./-]+:(?!latest$)[\w.-]*\d+\.\d+[\w.-]*(@sha256:[0-9a-f]{64})?$")


def test_images_pinned() -> None:
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    images = {name: svc["image"] for name, svc in services.items() if "image" in svc}
    assert len(images) == len(services), "every service declares an image (built ones get a version tag)"
    floating = {n: i for n, i in images.items() if not PINNED.match(i)}
    assert not floating, f"unpinned images: {floating}"
