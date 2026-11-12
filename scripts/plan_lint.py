"""Mechanical consistency checks for docs/plan. Exit 1 on any error."""
import re, sys, pathlib, collections
P = pathlib.Path(__file__).resolve().parent.parent / "docs" / "plan"
readme = (P / "README.md").read_text()
cov = (P / "coverage.md").read_text()
slices = sorted(P.glob("S*.md"), key=lambda p: int(p.stem[1:]))
errs, warns = [], []
checklist = set(re.findall(r"\[ \] (S\d+\.\d+)", readme))
tasks, deps, acs_by_task, test_ids = {}, {}, {}, collections.defaultdict(list)
for f in slices:
    text = f.read_text()
    for m in re.finditer(r"^### (S\d+\.\d+[a-z]?) — (.+)$", text, re.M):
        tid = m.group(1)
        if tid in tasks: errs.append(f"duplicate task {tid}")
        start = m.end(); nxt = re.search(r"^### ", text[start:], re.M)
        body = text[start: start + nxt.start()] if nxt else text[start:]
        tasks[tid] = (f.name, m.group(2), body)
        d = re.search(r"^Deps: ([^·\n]+)", body, re.M)
        if not d: errs.append(f"{tid}: no Deps line")
        else: deps[tid] = d.group(1)
        acs = re.findall(r"^- \[ \] (AC\d+) (.+)$", body, re.M)
        if not acs: errs.append(f"{tid}: no ACs")
        nums = [int(a[0][2:]) for a in acs]
        if nums != list(range(1, len(nums) + 1)): errs.append(f"{tid}: AC numbering {nums}")
        acs_by_task[tid] = acs
        for ac, line in acs:
            gate_ref = re.search(r"(S\d+\.\d+ AC\d+|ACs pass|`make ci` and `make test` pass|All S\d)", line)
            has_ver = re.search(r"`t:|`\$ |`M`|— `M|\bM\b user", line)
            if not has_ver and not gate_ref:
                errs.append(f"{tid} {ac}: no verification marker -> {line[:90]}")
            if re.search(r"\breport(s|ed)? (X|the|metric)\b", line) and not re.search(r"[<>≥≤=]", line):
                warns.append(f"{tid} {ac}: 'report' without threshold")
            for t in re.findall(r"`t:([^`]+)`", line):
                test_ids[t].append(f"{tid} {ac}")
heads = set(tasks)
for t in sorted(checklist - heads): errs.append(f"checklist has {t} but no task heading")
for t in sorted(heads - checklist): errs.append(f"task {t} missing from README checklist")
for tid, d in deps.items():
    for ref in re.findall(r"S\d+\.\d+", d):
        if ref not in heads: errs.append(f"{tid}: dep {ref} does not exist")
for t, users in test_ids.items():
    if len(users) > 1: errs.append(f"test {t} reused by {users} (AC not atomic)")
for ref in set(re.findall(r"\bS\d+\.\d+\b", cov)):
    if ref not in heads: errs.append(f"coverage.md references unknown {ref}")
cov_ids = set(re.findall(r"\bS\d+\.\d+\b", cov)); ranges = re.findall(r"S(\d+)\.(\d+)[–-]S\1\.(\d+)", cov)
for s_, a, b in ranges:
    for i in range(int(a), int(b) + 1): cov_ids.add(f"S{s_}.{i:02d}")
for s_ in re.findall(r"\bS(\d+)\.\*", cov):
    cov_ids |= {t for t in heads if t.startswith(f"S{s_}.")}
for t in sorted(heads - cov_ids):
    name = tasks[t][1]
    if "Gate" not in name: warns.append(f"task {t} ({name}) not referenced in coverage.md")
n_ac = sum(len(v) for v in acs_by_task.values())
print(f"tasks={len(tasks)} acs={n_ac} tests={len(test_ids)} errors={len(errs)} warnings={len(warns)}")
for e in errs: print("ERROR", e)
for w in warns: print("WARN ", w)
sys.exit(1 if errs else 0)
