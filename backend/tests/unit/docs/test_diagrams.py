"""The committed chat-turn diagram is generated from the real turn graph."""

import pathlib
import re

from app.chat.turn import GRAPH

ROOT = pathlib.Path(__file__).resolve().parents[4]
IMG = ROOT / "docs" / "img"


def test_turn_nodes_match_steps() -> None:
    committed = (IMG / "chat_turn.mmd").read_text()
    assert committed.strip() == GRAPH.describe().strip(), "stale: run `make diagrams`"
    for node in GRAPH.nodes:
        assert f'{node}["{node}"]' in committed


def test_architecture_doc_links_diagrams() -> None:
    text = (ROOT / "ARCHITECTURE.md").read_text()
    for name in ("topology", "ingestion", "chat_turn"):
        assert f"docs/img/{name}.mmd" in text
        assert (IMG / f"{name}.mmd").exists()
    blocks = re.findall(r"```mermaid\n(.*?)```", text, re.S)
    committed = (IMG / "chat_turn.mmd").read_text().strip()
    assert any(b.strip() == committed for b in blocks), "ARCHITECTURE.md turn diagram is stale"
