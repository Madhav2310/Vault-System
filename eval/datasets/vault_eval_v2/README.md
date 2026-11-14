# vault_eval_v2

Gold set v2 (plan S21): about 300 items, atomic claims. `golden.yaml` has a top-level `items:` list; load it with
`eval.loader.load_dataset_v2()` (schema: `eval.schema.GoldItemV2`). v1 (`../vault_eval_v1`) stays loadable with
`eval.loader.load_dataset()` for old reports.

## Item fields (unknown keys are rejected)

| Field | Type | Notes |
|---|---|---|
| `id` | str | unique |
| `stratum` | enum | see below |
| `question` | str | the (final) user question |
| `source_doc` | str | clustering key; at most 3 non-dropped items per value (dataset-level check) |
| `answer_type` | `answer` / `abstain` / `partial` | |
| `required_claims` | list[str] | 1 to 6 each checkable alone for `answer`/`partial`; none for `abstain` |
| `optional_claims` | list[str] | |
| `alternates` | dict[str, list[str]] | key must equal one of the item's claims; value = accepted variants (number formats, units, synonyms) |
| `gold_evidence` | list[{doc, sentence_start, sentence_end}] | half-open sentence range of the document; none for `abstain` |
| `must_not_contain` | list[str] | required for `injection` |
| `doc_ids` | list[str] or null | scope; null = whole vault |
| `prior_turns` | list[str] | earlier user turns; required for (and only for) `multi_turn` |
| `audit_status` | `draft` / `verified` / `dropped` | default `draft`; `dropped` needs `audit_note` and does not count toward the cap |
| `audit_note` | str or null | |

## Strata (matrix order, target n)

1. `single_span` 40 · 2. `conditional` 20 · 3. `table_lookup` 25 · 4. `numeric_calc` 20 · 5. `multi_span` 20 ·
6. `multi_hop` 20 · 7. `compare_docs` 20 · 8. `conflict` 15 · 9. `aggregation` 10 · 10. `unanswerable` 25
(must be `abstain`) · 11. `false_premise` 15 · 12. `versioned` 15 · 13. `injection` 20 · 14. `multi_turn` 10 ·
15. `formatting` 5.

Check: `cd backend && uv run pytest tests/unit/eval/test_schema_v2.py`
