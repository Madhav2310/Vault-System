# Vault-Eval v1

Golden evaluation set for the Vault document system: **122 items over 15 documents**, 86 of them with **third-party expert or annotator labels** (CUAD, QASPER, FinanceBench) and 36 authored items whose ground truth is derived from those labels wherever possible.

Status: **draft — awaiting human review** (`status: draft` on every item). Review is scheduled at the end of the build (user decision, 2026-11-12); until then, numbers produced with this set are labelled *pre-review*.

## Rebuild

```bash
make eval-fetch   # eval/build/fetch_sources.sh: third-party sources → eval/sources/ (gitignored, ~300 MB)
make eval-build   # runs, under backend/'s uv `eval` dependency group:
                  #   build_external.py   CUAD/QASPER/FinanceBench items, gold quotes located in PDF text
                  #   build_authored.py   authored docs + items, quote verification, splits
# extractor-config sweep (stage 1 of the convergence engine), run separately:
cd backend && uv run --frozen --group eval python ../eval/build/extraction_sweep.py
```

## Composition

| Category | Items | Train | Test |
|---|---|---|---|
| lookup | 68 | 27 | 41 |
| unanswerable | 15 | 6 | 9 |
| compare | 8 | 2 | 6 |
| multi_turn | 6 | 3 | 3 |
| conflict | 6 | 2 | 4 |
| identifier | 6 | 3 | 3 |
| table | 5 | 2 | 3 |
| summarize | 5 | 3 | 2 |
| meta | 2 | 1 | 1 |
| injection | 1 | 1 | 0 |

Sources: CUAD 54, QASPER 18, FinanceBench 14, authored 36.

## Documents

| ID | Type | Pages | Source | License |
|---|---|---|---|---|
| `cuad_consulting` | contract | 8 | CUAD v1 | CC BY 4.0 |
| `cuad_endorsement` | contract | 6 | CUAD v1 | CC BY 4.0 |
| `cuad_cohosting` | contract | 12 | CUAD v1 | CC BY 4.0 |
| `cuad_distributor` | contract | 16 | CUAD v1 | CC BY 4.0 |
| `cuad_license` | contract | 17 | CUAD v1 | CC BY 4.0 |
| `cuad_franchise` | contract | 9 | CUAD v1 | CC BY 4.0 |
| `qasper_factweet` | paper | 8 | QASPER v0.3 (arXiv 1910.06592) | QASPER annotations CC BY 4.0; paper per arXiv license |
| `qasper_hatespeech` | paper | 8 | QASPER v0.3 (arXiv 1910.03814) | QASPER annotations CC BY 4.0; paper per arXiv license |
| `qasper_kurdish` | paper | 4 | QASPER v0.3 (arXiv 1911.13087) | QASPER annotations CC BY 4.0; paper per arXiv license |
| `fb_ulta_q4` | financial_filing | 9 | FinanceBench open sample | CC BY-NC 4.0 (non-commercial; assignment use) |
| `fb_3m_10q` | financial_filing | 92 | FinanceBench open sample | CC BY-NC 4.0 (non-commercial; assignment use) |
| `fb_amd_10k` | financial_filing | 121 | FinanceBench open sample | CC BY-NC 4.0 (non-commercial; assignment use) |
| `auth_amendment` | contract | — | authored for Vault-Eval | CC0 (authored) |
| `auth_side_letter` | contract | — | authored for Vault-Eval | CC0 (authored) |
| `auth_travel_policy` | policy | — | authored for Vault-Eval | CC0 (authored) |

## Design choices

- **Chunker-independent gold.** Gold evidence is stored as verbatim quotes plus page numbers, not chunk IDs, so the set survives chunking changes. The harness maps quotes to chunks at run time.
- **Every gold quote is located in the production extractor's output.** 136 exact and 19 fuzzy (≥88) locations, 0 unlocated. FinanceBench's labelled evidence pages agree with the located pages 14/14.
- **Expert-labelled unanswerables.** CUAD "no such clause" labels and QASPER "unanswerable" labels give 15 abstention items whose ground truth isn't ours.
- **Conflicts are real documents.** An amendment (Markdown) and a side letter (DOCX) contradict specific CUAD-labelled clauses; two control items check that non-conflicting facts aren't flagged.
- **Document-level holdout.** Test-only documents: auth_side_letter, cuad_license, fb_3m_10q, qasper_kurdish. The tuner never sees their content, so test scores measure generalization to unseen documents, not memorized prompts.
- **Smoke subset** (21 items, ≥2 per category) for nightly runs and fast tuner rounds.

## Extraction finding that shaped the production extractor

`extraction_sweep.py` scores pdfplumber configurations by how many expert gold quotes they make locatable:

| Config | Located | ≥97 similarity | Papers |
|---|---|---|---|
| default | 86/100 | 76 | 9/23 |
| text_flow | 93/100 | 77 | 16/23 |
| text_flow+xtr0.15 | 99/100 | 92 | 22/23 |
| xtr0.15 | 92/100 | 85 | 15/23 |

Default extraction merged words in justified two-column text ("atherreal-timetweetsfrom") and interleaved columns; content-stream order plus a font-relative word gap (`use_text_flow=True, x_tolerance_ratio=0.15`) fixes both with no regression on contracts or filings. This is now the production default (design §6.4).

## Licenses

CUAD: CC BY 4.0 (The Atticus Project). QASPER annotations: CC BY 4.0 (AllenAI); papers under their arXiv licenses and used for evaluation only. FinanceBench open sample: CC BY-NC 4.0 (non-commercial, assignment use). Authored documents: CC0. Third-party files are fetched, not committed.
