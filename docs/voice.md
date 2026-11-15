# Voice

How Vault talks. It reads like a calm, kind colleague who has read your documents. Applies to every code-written message (`backend/app/explain/templates.py`, always on) and the UI microcopy.

## Rules

1. **Calm, warm, direct.** Plain words. Short sentences. The answer comes first.
2. **Say what was found, then what was not,** kindly and plainly: "The agreement sets the term at 12 months. It does not say how it renews."
3. **Never blame the user.** No "you must have", no "invalid", no "you did not". The system is the one that could not.
4. **No corporate filler.** No "Certainly", "Great question", "I hope this helps", "Unfortunately", "We apologise for any inconvenience".
5. **No exclamation marks, no emojis, no shouting capitals** (acronyms like PDF and UTC are fine).
6. **Same language as the user.**
7. **Do not over-apologise.** At most one brief "sorry", and only when something on our side failed. Most messages need none.
8. **Numbers, names, dates and defined terms stay exact.** Warmth never rounds, paraphrases or adds a fact.
9. **A gentle next step whenever we cannot answer.** One short sentence: rephrase, name the document, upload, try again.
10. **Warmth never loosens grounding.** The voice changes wording only. Citations, abstention and evidence rules are unchanged.

## Avoid and prefer

**Answer**
- Avoid: "Notice period: 30 days. Termination: either party."
- Prefer: "Either party can end the agreement with 30 days' written notice."

**Partial**
- Avoid: "The documents provided do not state an answer to: renewal terms."
- Prefer: "That is what your documents back up. I could not find the rest, so please check the document itself or name the section you have in mind."

**Not found**
- Avoid: "I could not find anything in your 2 documents that answers this."
- Prefer: "I could not find this in your 2 documents. Try asking in different words, or name the document you have in mind."

**Conflict**
- Avoid: "The sources disagree on a point, so both sides are shown with their citations."
- Prefer: "The sources differ on a point, so both sides are shown with their citations."

**Error**
- Avoid: "Something went wrong while answering. Please try again."
- Prefer: "That did not work on my side, and no answer was written. Please try again in a moment."

## Where it lives

| Surface | Mechanism | Test |
| --- | --- | --- |
| Abstain, not found, quota, rate limit, stale, errors, reason lines | `app/explain/templates.py` (deterministic, always on) | `tests/unit/explain/test_voice_templates.py` |
| UI microcopy | follows this page | UI e2e |
