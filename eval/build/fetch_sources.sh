#!/usr/bin/env bash
# Fetch third-party eval sources into eval/sources/ (gitignored). Idempotent.
set -euo pipefail
cd "$(dirname "$0")/../sources" 2>/dev/null || { mkdir -p "$(dirname "$0")/../sources"; cd "$(dirname "$0")/../sources"; }
[ -f cuad/CUADv1.json ] || { curl -sL -o cuad_data.zip https://github.com/TheAtticusProject/cuad/raw/main/data.zip && unzip -q -o cuad_data.zip -d cuad && rm cuad_data.zip; }
[ -d cuad_full/CUAD_v1 ] || { curl -sL -o CUAD_v1.zip https://zenodo.org/records/4595826/files/CUAD_v1.zip && unzip -q -o CUAD_v1.zip -d cuad_full && rm CUAD_v1.zip; }
[ -f qasper-dev-v0.3.json ] || { curl -sL -o qasper.tgz https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.3.tgz && tar -xzf qasper.tgz && rm qasper.tgz; }
mkdir -p qasper_pdfs fb_pdfs
for id in 1910.06592 1910.03814 1911.13087; do [ -f qasper_pdfs/$id.pdf ] || curl -sL -A "vault-eval-fetch" -o qasper_pdfs/$id.pdf https://arxiv.org/pdf/$id; done
[ -f fb_questions.jsonl ] || curl -sL -o fb_questions.jsonl https://raw.githubusercontent.com/patronus-ai/financebench/main/data/financebench_open_source.jsonl
[ -f fb_docs.jsonl ] || curl -sL -o fb_docs.jsonl https://raw.githubusercontent.com/patronus-ai/financebench/main/data/financebench_document_information.jsonl
for d in AMD_2022_10K ULTABEAUTY_2023Q4_EARNINGS 3M_2023Q2_10Q; do [ -f fb_pdfs/$d.pdf ] || curl -sL -o fb_pdfs/$d.pdf "https://raw.githubusercontent.com/patronus-ai/financebench/main/pdfs/$d.pdf"; done
echo "sources ready"
