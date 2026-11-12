"""Renumber tasks in docs/plan/S*.md by their order in each file, rewriting every reference.

New tasks may be inserted with temporary IDs like `S1.N1`, `S1.N2` (any `S<k>.N<j>`).
Run: python3 scripts/plan_renumber.py   → then python3 scripts/plan_lint.py
References rewritten in: all docs/plan/*.md (Deps lines, ACs, coverage.md, README checklist regenerated).
"""
import pathlib
import re

P = pathlib.Path(__file__).resolve().parent.parent / "docs" / "plan"
TASK = re.compile(r"^### (S(\d+)\.(N?\d+)) — ", re.M)
ID = r"S\d+\.N?\d+"

mapping: dict[str, str] = {}
slices = sorted(P.glob("S*.md"), key=lambda p: int(p.stem[1:]))
for f in slices:
    k = int(f.stem[1:])
    for i, m in enumerate(TASK.finditer(f.read_text()), start=1):
        mapping[m.group(1)] = f"S{k}.{i:02d}"

def rewrite(text: str) -> str:
    # ranges like S1.01–S1.18 (en dash or hyphen)
    def rng(m: re.Match) -> str:
        a, sep, b = m.group(1), m.group(2), m.group(3)
        return f"{mapping.get(a, a)}{sep}{mapping.get(b, b)}"
    # single pass: ranges and lone IDs in one regex, so no ID is rewritten twice
    pat = re.compile(rf"({ID})([–-])({ID})|(?<![\w.])({ID})(?![\w])")
    return pat.sub(lambda m: rng(m) if m.group(1) else mapping.get(m.group(4), m.group(4)), text)

changed = {k: v for k, v in mapping.items() if k != v}
for f in P.glob("*.md"):
    t = f.read_text()
    nt = rewrite(t)
    if nt != t:
        f.write_text(nt)

# regenerate README checklist
readme = P / "README.md"
r = readme.read_text()
done = set(re.findall(r"\[x\] (S\d+\.\d+)", r))  # preserve ticks; README IDs were already rewritten above
lines = []
for f in slices:
    k = int(f.stem[1:])
    ids = [m.group(1) for m in TASK.finditer(f.read_text())]
    lines.append(f"**S{k}** — " + " ".join(f"[{'x' if i in done else ' '}] {i}" +(" (gate)" if j == len(ids) - 1 else "") for j, i in enumerate(ids)))
r = re.sub(r"(Tick a box only when the task's definition of done is met\.\n\n)(.*)$", lambda m: m.group(1) + "\n\n".join(lines) + "\n", r, flags=re.S)
readme.write_text(r)
print(f"renumbered {len(changed)} task ids")
