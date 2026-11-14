# Judge calibration labels

`labeled.yaml` holds human labels for (item, answer) pairs. Schema: `schema.py`.

1. Collect answers as JSONL, one object per line: `{"item_id", "answer", "segments": [{"text", "passages": [...]}], "failed": true|false}`.
   `failed` is the failure signal used to oversample (about 60% of the export is failures).
2. `cd backend && PYTHONPATH=.. uv run python -m eval.judge --export-labels 200 --answers answers.jsonl --out ../eval/labels/labeled.yaml`
   writes the pairs with every label null. `split` is fixed here by a seeded hash of `source_doc`
   (15% few_shot, 40% dev, 45% frozen test), so one document never spans splits.
3. Label by editing the file. Judge each decision on its own, as a careful reader of the answer text only:
   - `claim_labels[k]` true if required claim k is stated by the answer (paraphrase, equivalent numbers and units
     count; extra detail is irrelevant), false otherwise.
   - `support_labels[s]` true if segment s is entailed by its own `passages` alone.
   - `abstain_label` (only for unanswerable / false_premise / versioned items) true if the answer declined or
     corrected the premise as required, false if it answered as asked.
   Leave a label null to skip it.
4. `python -m eval.judge --calibrate eval/labels/labeled.yaml --max-usd 3` runs each judge on the frozen test
   split and prints TPR, TNR and Cohen's kappa per judge (positive = entailed / supported / correct).
   Targets: kappa >= 0.75 and TNR >= 0.90. Never tune a prompt on `test`; use `dev` and `few_shot`.
