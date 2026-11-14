"""Tick ACs (and the README checkbox when all ACs are ticked) for plan tasks.

Usage: python3 scripts/plan_tick.py S0.04            # tick every AC of S0.04
       python3 scripts/plan_tick.py S0.06:AC1,AC3    # tick only those ACs
Then run scripts/plan_lint.py. Tick only with evidence recorded in docs/status/progress.md.
"""

import pathlib
import re
import sys

P = pathlib.Path(__file__).resolve().parent.parent / "docs" / "plan"

for arg in sys.argv[1:]:
    tid, _, acs = arg.partition(":")
    wanted = set(acs.split(",")) if acs else None
    f = P / f"{tid.split('.')[0]}.md"
    text = f.read_text()
    m = re.search(rf"^### {re.escape(tid)} — .*?(?=^### |\Z)", text, re.S | re.M)
    if not m:
        sys.exit(f"{tid}: not found in {f.name}")
    block = m.group(0)
    new = re.sub(
        r"^- \[ \] (AC\d+)",
        lambda a: f"- [x] {a.group(1)}" if wanted is None or a.group(1) in wanted else a.group(0),
        block,
        flags=re.M,
    )
    f.write_text(text.replace(block, new))
    done = not re.search(r"^- \[ \] AC", new, re.M)
    if done:
        readme = P / "README.md"
        readme.write_text(readme.read_text().replace(f"[ ] {tid} ", f"[x] {tid} ").replace(f"[ ] {tid}\n", f"[x] {tid}\n"))
    print(f"{tid}: {'complete' if done else 'partial'}")
