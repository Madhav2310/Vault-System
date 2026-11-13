"""Core-path dependency closure: every dependency of a core task (plan README, "Core path") is S0, another core
task, or an allowed gate override (S1.32, S2.21, S5.16). Exit 1 otherwise. Usage: python3 scripts/plan_core_closure.py"""
import sys
import re, pathlib
P = pathlib.Path(__file__).resolve().parent.parent / "docs" / "plan"
tasks, deps = {}, {}
for f in P.glob("S*.md"):
    t = f.read_text()
    for m in re.finditer(r"^### (S\d+\.\d+) — (.+)$", t, re.M):
        body = t[m.end(): (lambda n: m.end()+n.start() if n else len(t))(re.search(r"^### ", t[m.end():], re.M))]
        tasks[m.group(1)] = m.group(2)
        d = re.search(r"^Deps: ([^·\n]+)", body, re.M).group(1)
        refs = set(re.findall(r"S\d+\.\d+", d))
        for s_, a, b in re.findall(r"S(\d+)\.(\d+)[–-]S\1\.(\d+)", d):
            refs |= {f"S{s_}.{i:02d}" for i in range(int(a), int(b)+1)}
        deps[m.group(1)] = refs
readme = (P / "README.md").read_text()
section = readme[readme.index("### Core path") : readme.index("Rules for the core path")]
core = set(re.findall(r"S\d+\.\d+", section))
gates = {"S1.32", "S2.21", "S5.16"}
bad = {t: sorted(d for d in deps[t] if not d.startswith("S0.") and d not in core | gates) for t in sorted(core)}
bad = {t: v for t, v in bad.items() if v}
print(f"core tasks={len(core)} unresolved={bad}")
sys.exit(1 if bad or len(core) != 51 else 0)
