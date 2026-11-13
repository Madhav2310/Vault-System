"""Build the externally-labelled part of Vault-Eval v1 from CUAD, QASPER and FinanceBench.

Every gold quote is located in the PDF text extracted by pdfplumber (the production extractor),
so the dataset doubles as an extraction check: an unlocatable expert span is reported, not hidden.

Run: make eval-build   (or: cd backend && uv run --frozen --group eval python ../eval/build/build_external.py)   (after eval/build/fetch_sources.sh)
Output: eval/datasets/vault_eval_v1/external.yaml, eval/datasets/vault_eval_v1/documents.yaml
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import unicodedata

import pdfplumber
import yaml
from rapidfuzz import fuzz

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "eval" / "sources"
OUT = ROOT / "eval" / "datasets" / "vault_eval_v1"
REF = re.compile(r"\s*(BIBREF|TABREF|FIGREF|SECREF|INLINEFORM|DISPLAYFORM)\d*")
FUZZ_MIN = 88  # partial_ratio threshold for a located quote

CUAD_DOCS = {
    "cuad_consulting": "MEDALISTDIVERSIFIEDREIT,INC_05_18_2020-EX-10.1-CONSULTING AGREEMENT",
    "cuad_endorsement": "WARNINGMANAGEMENTSERVICESINC_12_10_1999-EX-10-ENDORSEMENT AGREEMENT",
    "cuad_cohosting": "BEYONDCOMCORP_08_03_2000-EX-10.2-CO-HOSTING AGREEMENT",
    "cuad_distributor": "ENTERTAINMENTGAMINGASIAINC_02_15_2005-EX-10.5-DISTRIBUTOR AGREEMENT",
    "cuad_license": "GpaqAcquisitionHoldingsInc_20200123_S-4A_EX-10.6_11951677_EX-10.6_License Agreement",
    "cuad_franchise": "SimplicityEsportsGamingCompany_20181130_8-K_EX-10.1_11444071_EX-10.1_Franchise Agreement",
}
# CUAD category -> natural question (answerable categories, in priority order)
CUAD_POS = {
    "Parties": "Who are the parties to this agreement?",
    "Agreement Date": "What is the date of this agreement?",
    "Effective Date": "When does this agreement take effect?",
    "Expiration Date": "When does this agreement expire, or what is its initial term?",
    "Renewal Term": "Does the agreement renew, and on what terms?",
    "Notice Period To Terminate Renewal": "How much notice is needed to stop the agreement from renewing?",
    "Governing Law": "Which jurisdiction's law governs this agreement?",
    "Termination For Convenience": "Can a party terminate this agreement without cause, and how?",
    "Exclusivity": "Does the agreement grant any exclusivity?",
    "Non-Compete": "Is there a non-compete obligation, and what does it restrict?",
    "Anti-Assignment": "Can this agreement be assigned to someone else?",
    "Cap On Liability": "Is liability capped under this agreement?",
    "Revenue/Profit Sharing": "How are revenues, fees or profits shared?",
    "Minimum Commitment": "Is there a minimum purchase or commitment?",
    "License Grant": "What license is granted under this agreement?",
    "Insurance": "What insurance must be maintained?",
    "Audit Rights": "Does either party have audit or inspection rights?",
    "Ip Ownership Assignment": "Who owns intellectual property created under the agreement?",
    "Post-Termination Services": "What obligations continue after termination?",
    "Change Of Control": "What happens if a party undergoes a change of control?",
    "Warranty Duration": "How long do warranties last?",
}
# Categories used as unanswerable probes when the expert label is "no clause"
CUAD_NEG = {
    "Source Code Escrow": "Is there a source code escrow arrangement?",
    "Most Favored Nation": "Does the agreement include a most-favored-nation pricing clause?",
    "Liquidated Damages": "What liquidated damages does the agreement specify?",
    "Covenant Not To Sue": "Does either party covenant not to sue the other?",
    "Third Party Beneficiary": "Which third parties are beneficiaries of this agreement?",
    "Non-Disparagement": "Is there a non-disparagement obligation?",
    "Rofr/Rofo/Rofn": "Is there a right of first refusal or first offer?",
    "Uncapped Liability": "Which liabilities are uncapped?",
}
CUAD_POS_N, CUAD_NEG_N = 7, 2

QASPER_DOCS = {
    "qasper_factweet": "1910.06592",
    "qasper_hatespeech": "1910.03814",
    "qasper_kurdish": "1911.13087",
}
FB_DOCS = {
    "fb_ulta_q4": "ULTABEAUTY_2023Q4_EARNINGS",
    "fb_3m_10q": "3M_2023Q2_10Q",
    "fb_amd_10k": "AMD_2022_10K",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace("­", "")
    return re.sub(r"\s+", " ", s).strip().lower()


def sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# Production extraction config, chosen by eval/build/extraction_sweep.py (99/100 gold quotes located vs 86/100 default).
EXTRACT_CFG = {"use_text_flow": True, "x_tolerance_ratio": 0.15}


def page_texts(pdf_path: pathlib.Path) -> list[str]:
    with pdfplumber.open(pdf_path) as pdf:
        return [norm(pg.dedupe_chars().extract_text(**EXTRACT_CFG) or "") for pg in pdf.pages]


def locate(quote: str, pages: list[str]) -> dict:
    """Find a quote: exact normalized substring first, then fuzzy partial match per page."""
    q = norm(REF.sub("", quote))
    if len(q) > 400:  # long expert spans: match a distinctive 300-char head
        q = q[:300]
    for i, t in enumerate(pages):
        if q and q in t:
            return {"page": i + 1, "match": "exact", "score": 100}
    best = (0.0, None)
    for i, t in enumerate(pages):
        sc = fuzz.partial_ratio(q, t) if q else 0
        if sc > best[0]:
            best = (sc, i + 1)
    if best[0] >= FUZZ_MIN:
        return {"page": best[1], "match": "fuzzy", "score": round(best[0], 1)}
    return {"page": None, "match": "not_found", "score": round(best[0], 1)}


def find_pdf(title: str) -> pathlib.Path:
    for p in (SRC / "cuad_full").rglob("*"):
        if p.suffix.lower() == ".pdf" and p.stem == title:
            return p
    raise FileNotFoundError(title)


def build_cuad(docs: dict, items: list) -> None:
    data = {d["title"]: d for d in json.load(open(SRC / "cuad" / "CUADv1.json"))["data"]}
    for doc_id, title in CUAD_DOCS.items():
        pdf = find_pdf(title)
        pages = page_texts(pdf)
        docs[doc_id] = {
            "source": "CUAD v1",
            "license": "CC BY 4.0",
            "file": str(pdf.relative_to(ROOT)),
            "sha256": sha256(pdf),
            "pages": len(pages),
            "type": "contract",
            "title": title,
        }
        qas = {
            re.search(r'related to "([^"]+)"', q["question"]).group(1): q
            for q in data[title]["paragraphs"][0]["qas"]
        }
        pos = neg = 0
        for cat, question in CUAD_POS.items():
            if pos >= CUAD_POS_N or cat not in qas or not qas[cat]["answers"]:
                continue
            spans = list(dict.fromkeys(a["text"] for a in qas[cat]["answers"]))[:3]
            gold = [{"quote": s, **locate(s, pages)} for s in spans]
            if not any(g["page"] for g in gold):
                items.append({"id": f"{doc_id}:{cat}", "_rejected": "gold_not_located", "gold": gold})
                continue
            items.append(
                {
                    "id": f"{doc_id}:{cat.lower().replace(' ', '_').replace('/', '_')}",
                    "source": "CUAD",
                    "category": "lookup",
                    "doc_ids": [doc_id],
                    "question": question,
                    "expected_answer": " | ".join(spans),
                    "answer_type": "extractive",
                    "gold": [g for g in gold if g["page"]],
                    "expected_coverage": "supported",
                    "grading": "judge",
                    "cuad_category": cat,
                    "status": "draft",
                }
            )
            pos += 1
        for cat, question in CUAD_NEG.items():
            if neg >= CUAD_NEG_N or cat not in qas or qas[cat]["answers"]:
                continue
            items.append(
                {
                    "id": f"{doc_id}:no_{cat.lower().replace(' ', '_').replace('/', '_')}",
                    "source": "CUAD",
                    "category": "unanswerable",
                    "doc_ids": [doc_id],
                    "question": question,
                    "expected_answer": "Not addressed in the document (CUAD expert label: no such clause).",
                    "answer_type": "unanswerable",
                    "gold": [],
                    "expected_coverage": "insufficient",
                    "grading": "abstention",
                    "cuad_category": cat,
                    "status": "draft",
                }
            )
            neg += 1


def build_qasper(docs: dict, items: list) -> None:
    q = {**json.load(open(SRC / "qasper-dev-v0.3.json")), **json.load(open(SRC / "qasper-train-v0.3.json"))}
    for doc_id, pid in QASPER_DOCS.items():
        pdf = SRC / "qasper_pdfs" / f"{pid}.pdf"
        pages = page_texts(pdf)
        p = q[pid]
        docs[doc_id] = {
            "source": "QASPER v0.3 (arXiv " + pid + ")",
            "license": "QASPER annotations CC BY 4.0; paper per arXiv license",
            "file": str(pdf.relative_to(ROOT)),
            "sha256": sha256(pdf),
            "pages": len(pages),
            "type": "paper",
            "title": p["title"],
        }
        n_ans = n_un = 0
        for k, qa in enumerate(p["qas"]):
            a = qa["answers"][0]["answer"]
            if a["unanswerable"] and n_un < 1:
                items.append(
                    {
                        "id": f"{doc_id}:q{k}",
                        "source": "QASPER",
                        "category": "unanswerable",
                        "doc_ids": [doc_id],
                        "question": qa["question"],
                        "expected_answer": "Not answerable from the paper (QASPER annotator label).",
                        "answer_type": "unanswerable",
                        "gold": [],
                        "expected_coverage": "insufficient",
                        "grading": "abstention",
                        "status": "draft",
                    }
                )
                n_un += 1
                continue
            if a["unanswerable"] or n_ans >= 5:
                continue
            ans = (
                a["free_form_answer"]
                or ("yes" if a["yes_no"] else "no" if a["yes_no"] is False else "")
                or "; ".join(a["extractive_spans"] or [])
            )
            ev = [
                e for e in (a.get("highlighted_evidence") or a["evidence"]) if e and "FLOAT SELECTED" not in e
            ]
            if not ans or REF.search(ans) or not ev:
                continue
            gold = [{"quote": REF.sub("", e).strip(), **locate(e, pages)} for e in ev[:2]]
            if not any(g["page"] for g in gold):
                items.append({"id": f"{doc_id}:q{k}", "_rejected": "gold_not_located", "gold": gold})
                continue
            items.append(
                {
                    "id": f"{doc_id}:q{k}",
                    "source": "QASPER",
                    "category": "lookup",
                    "doc_ids": [doc_id],
                    "question": qa["question"],
                    "expected_answer": ans,
                    "answer_type": "yes_no" if a["yes_no"] is not None else "free",
                    "gold": [g for g in gold if g["page"]],
                    "expected_coverage": "supported",
                    "grading": "judge",
                    "status": "draft",
                }
            )
            n_ans += 1


def build_fb(docs: dict, items: list) -> None:
    rows = [json.loads(line) for line in open(SRC / "fb_questions.jsonl")]
    for doc_id, name in FB_DOCS.items():
        pdf = SRC / "fb_pdfs" / f"{name}.pdf"
        pages = page_texts(pdf)
        docs[doc_id] = {
            "source": "FinanceBench open sample",
            "license": "CC BY-NC 4.0 (non-commercial; assignment use)",
            "file": str(pdf.relative_to(ROOT)),
            "sha256": sha256(pdf),
            "pages": len(pages),
            "type": "financial_filing",
            "title": name,
        }
        for r in (r for r in rows if r["doc_name"] == name):
            gold = []
            for e in r["evidence"]:
                loc = locate(e["evidence_text"], pages)
                gold.append(
                    {"quote": e["evidence_text"][:600], "labelled_page": e["evidence_page_num"] + 1, **loc}
                )
            numeric = bool(re.search(r"\d", r["answer"])) and len(r["answer"]) < 40
            items.append(
                {
                    "id": f"{doc_id}:{r['financebench_id']}",
                    "source": "FinanceBench",
                    "category": "table" if numeric else "lookup",
                    "doc_ids": [doc_id],
                    "question": r["question"],
                    "expected_answer": r["answer"],
                    "answer_type": "numeric" if numeric else "free",
                    "gold": gold,
                    "expected_coverage": "supported",
                    "grading": "numeric_tolerance" if numeric else "judge",
                    "fb_question_type": r["question_type"],
                    "fb_reasoning": r.get("question_reasoning"),
                    "status": "draft",
                }
            )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    docs: dict = {}
    items: list = []
    build_cuad(docs, items)
    build_qasper(docs, items)
    build_fb(docs, items)
    kept = [i for i in items if "_rejected" not in i]
    rejected = [i for i in items if "_rejected" in i]
    yaml.safe_dump(
        {"documents": docs}, open(OUT / "documents.yaml", "w"), sort_keys=False, allow_unicode=True, width=120
    )
    yaml.safe_dump(
        {"items": kept}, open(OUT / "external.yaml", "w"), sort_keys=False, allow_unicode=True, width=120
    )
    yaml.safe_dump(
        {"rejected": rejected},
        open(OUT / "external_rejected.yaml", "w"),
        sort_keys=False,
        allow_unicode=True,
        width=120,
    )
    locs = [g for i in kept for g in i["gold"]]
    print(
        f"docs={len(docs)} items={len(kept)} rejected={len(rejected)} "
        f"gold_exact={sum(g['match'] == 'exact' for g in locs)} gold_fuzzy={sum(g['match'] == 'fuzzy' for g in locs)} "
        f"gold_not_found={sum(g['match'] == 'not_found' for g in locs)}"
    )
    fbpg = [g for i in kept if i["source"] == "FinanceBench" for g in i["gold"]]
    print(
        f"financebench page agreement (located page == labelled page): "
        f"{sum(g['page'] == g['labelled_page'] for g in fbpg)}/{len(fbpg)}"
    )


if __name__ == "__main__":
    main()
