# S22 paired A/B decisions

Rule (docs/plan/S22.md): adopt only if the primary metric improves with the paired CI excluding 0, or it is non-inferior and the secondary metric improves. Otherwise keep the default and record the result.

| Date | Task | Arm vs default | Set / split | Metric | Default | Arm | Δ [95% CI, clustered] | MDE | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-14 | S22.03 | lexical@2 (`retrieval@2-lexical2`) vs `retrieval@2` | v1 set, all 100 retrieval-scored items (few numeric queries) | recall@8 | 0.7652 | 0.7602 | −0.0050 [−0.0317, +0.0150] | 0.034 | no detectable difference; default unchanged. MRR@40 0.544 vs 0.533. Re-run on the v2 table_lookup and numeric_calc strata, where number variants should matter, before rejecting. Regenerate: `make eval-retrieval-fast RETR_CONFIG=<cfg>` for both configs then `python -m eval.paired A.json B.json --metric recall@8`. |
