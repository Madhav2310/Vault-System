"""Write the chat-turn Mermaid source from the real turn graph.

Run through the backend venv: `make diagrams`, or
`cd backend && uv run --frozen python ../scripts/gen_turn_diagram.py`. The output is
`docs/img/chat_turn.mmd`, which `test_diagrams.py` compares with `GRAPH.describe()`.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.chat.turn import GRAPH  # noqa: E402

OUT = ROOT / "docs" / "img" / "chat_turn.mmd"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(GRAPH.describe() + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
