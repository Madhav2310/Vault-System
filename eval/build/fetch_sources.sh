#!/usr/bin/env bash
# Fetch third-party eval sources into eval/sources/ (gitignored). Idempotent; fails loudly.
# Downloads resume (-C -) and retry, so a dropped connection can be finished by re-running.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)/sources"
mkdir -p "$SRC"
cd "$SRC"
get() { curl -fsSL --retry 8 --retry-all-errors --connect-timeout 30 -C - "${@:3}" -o "$1" "$2"; }

if [ ! -f cuad/CUADv1.json ]; then
  get cuad_data.zip https://github.com/TheAtticusProject/cuad/raw/main/data.zip
  unzip -q -o cuad_data.zip -d cuad
  rm cuad_data.zip
fi
if [ ! -d cuad_full/CUAD_v1 ]; then
  get CUAD_v1.zip https://zenodo.org/records/4595826/files/CUAD_v1.zip
  unzip -q -o CUAD_v1.zip -d cuad_full
  rm CUAD_v1.zip
fi
if [ ! -f qasper-dev-v0.3.json ]; then
  get qasper.tgz https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.3.tgz
  tar -xzf qasper.tgz
  rm qasper.tgz
fi
mkdir -p qasper_pdfs fb_pdfs
for id in 1910.06592 1910.03814 1911.13087; do
  [ -f "qasper_pdfs/$id.pdf" ] || get "qasper_pdfs/$id.pdf" "https://arxiv.org/pdf/$id" -A vault-eval-fetch
done
[ -f fb_questions.jsonl ] || get fb_questions.jsonl https://raw.githubusercontent.com/patronus-ai/financebench/main/data/financebench_open_source.jsonl
[ -f fb_docs.jsonl ] || get fb_docs.jsonl https://raw.githubusercontent.com/patronus-ai/financebench/main/data/financebench_document_information.jsonl
for d in AMD_2022_10K ULTABEAUTY_2023Q4_EARNINGS 3M_2023Q2_10Q; do
  [ -f "fb_pdfs/$d.pdf" ] || get "fb_pdfs/$d.pdf" "https://raw.githubusercontent.com/patronus-ai/financebench/main/pdfs/$d.pdf"
done
echo "sources ready"
