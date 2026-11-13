# What makes an AI/LLM engineering take-home (RAG / chat-with-docs backend) stand out, 2023-2026

Scope note: this covers how reviewers evaluate take-homes, which is the context for the "AI-Powered Vault Document System" spec (graded equally on AI-first development, product thinking, and technical implementation; "fully functional subset over partially implemented features"; README + AI_USAGE.md + demo; "think about scale: 1000s of documents").

Evidence quality warning: I found **no published take-home rubric from an AI lab or AI-product company**. Most search results are candidate-advice blogs and interview-prep sites (techinterview.org, prachub, career blogs), which give opinion without primary data. The most credible sources are (a) named hiring managers writing first-hand (Gergely Orosz; Simon Newton at Canva), (b) the interviewing.io candidate survey, (c) Hamel Husain and Shreya Shankar on evals (practitioner authority, not hiring), and (d) HN comments from people who say they review take-homes. Each finding below is tagged **[EVIDENCE]** (explicit statement by a reviewer or company, or survey data) or **[OPINION]** (advice blog or unsourced).

## 1. What engineering managers and hiring posts say they look for (clarity, scope judgment, tests, README, tradeoffs, demo)

### Takeaway
Reviewers check runnability first ("does it work at all? can I run it from your setup notes?"), then correctness and edge-case handling, then code quality and tests. The README is the most commonly missed differentiator: it should state what was built, what was deliberately not built, why the design choices were made, and what would come next. Sloppiness in basics (unhandled edge cases) sinks submissions that otherwise work.

### Cited Findings
- **[EVIDENCE, but 2019, outside the 2023-2026 window]** Gergely Orosz (hiring manager at Uber/Skype, later The Pragmatic Engineer) says documentation "is such a miss on 80% of submissions I've seen." Good documentation covers "Notes on starting the app or setting it up, for the reviewer", a "Summary of what was built and what was (deliberately) not", "Context on design decisions and framework choices", and "Areas for improvements." — [Gergely Orosz, DEV, Aug 2019](https://dev.to/gergelyorosz/9-insider-tips-to-ace-your-next-takehome-project-for-frontend-fullstack-and-mobile-interviews-41nn)
- **[EVIDENCE, 2019]** Same source: reviewers first ask "Does it work at all?" and whether they can run it by following the setup notes. "One of the most common reasons I see reviewers rejecting takehomes that do work is sloppy work", meaning unhandled basic edge cases like empty input or special characters. — [Orosz, DEV](https://dev.to/gergelyorosz/9-insider-tips-to-ace-your-next-takehome-project-for-frontend-fullstack-and-mobile-interviews-41nn)
- **[EVIDENCE, 2019]** Same source: reviewers check whether business logic has unit tests that pass. "The more senior the position the company is hiring for, the more they'll expect higher quality", and seniors are expected to cover architecture and testing. Also: "Most developers work really well when they know what edge cases to cover", so list edge cases before coding. — [Orosz, DEV](https://dev.to/gergelyorosz/9-insider-tips-to-ace-your-next-takehome-project-for-frontend-fullstack-and-mobile-interviews-41nn)
- **[EVIDENCE, 2019]** Same source: heavy libraries that do most of the work hurt, because "They won't know how well you would have fared without that specific library." — [Orosz, DEV](https://dev.to/gergelyorosz/9-insider-tips-to-ace-your-next-takehome-project-for-frontend-fullstack-and-mobile-interviews-41nn)
- **[EVIDENCE, HN, 2025]** A reviewer whose company sets take-homes: "We leave ambiguities in there and there are some Easter eggs in the dataset". The assignment is deliberately underspecified, and candidates are judged on how they handle the ambiguity. — [HN, stingraycharles, 2025-05-14, thread "I failed a take-home assignment from Kagi Search"](https://news.ycombinator.com/item?id=43980511)
- **[EVIDENCE, HN, 2025]** In the same thread, a commenter describes a reviewer who prefers polish and tight scope over ambitious design: "the project that passes with flying colors has keyboard shortcuts for everything." — [HN, saagarjha, 2025-05-14](https://news.ycombinator.com/item?id=43982643)
- **[EVIDENCE, survey]** interviewing.io surveyed about 700 users. Over 80% said take-homes should take at most 4 hours, and a plurality said 2 hours. Candidates report that "Companies will say, 'don't spend more than X hours on it,' but then it actually takes 5X that amount." About 32% were told the code would be discussed in a later round. Lack of feedback was the top complaint. The article does not describe company grading rubrics. — [Aline Lerner, interviewing.io](https://interviewing.io/blog/why-engineers-dont-like-take-homes-and-how-companies-can-fix-them)
- **[EVIDENCE, process]** Wikimedia distributes take-home grading across the team using a shared rubric spreadsheet with items and weights "to minimize bias". This shows that rubric-based, multi-reviewer grading happens in practice. — [Wikimedia Phabricator T322072](https://phabricator.wikimedia.org/T322072)
- **[OPINION]** Advice blogs agree that tradeoffs and decisions should be written in the README: a "Decisions and Tradeoffs" section is "one of the most important sections of the readme", the README is "the first thing reviewers will look into ... probably even before your resume", and "three good tests are better than zero." These came from aggregated search snippets of candidate-advice posts and were not verified first-hand. — [GreatFrontEnd](https://www.greatfrontend.com/blog/frontend-take-home-assignment); [tech-in-view](https://tech-in-view.vercel.app/blog/software-engineer-take-home-assignment-tips); [levelup.gitconnected](https://levelup.gitconnected.com/take-home-assignments-what-interviewers-actually-look-for-3b1498c2f989)

### Inferences
- The spec's own line ("we prefer a fully functional subset over partially implemented features") lines up with the reviewer evidence: runnability and finished, edge-case-handled core paths come before breadth.
- The README order should follow the reviewer's order: (1) how to run it, in one command, (2) what works, with a demo, (3) what was deliberately cut and why, (4) design decisions and tradeoffs, (5) what's next. A "Not built / deliberately deferred" section is a direct answer to the scope-judgment criterion.
- The Kagi "Easter eggs" pattern suggests this spec's vague parts (e.g. "AI insights", "metrics APIs") may be deliberate. Writing down the interpretation and the assumptions is part of what gets graded.
- A follow-up code walkthrough is common (about 32% in the survey), so every design choice should be one the author can defend live. Heavy unexplained machinery is a risk.

### Gaps
- No 2023-2026 first-hand essay from a named hiring manager matched Orosz's 2019 piece in specificity. Search results for 2024-2026 were mostly SEO advice sites.
- I couldn't find a public HN thread where a reviewer lists README/tests/runnability as explicit rejection criteria. The closest comments are candidates relaying reviewer feedback.

## 2. AI-engineering specifics: RAG quality, evals, prompt engineering docs, AI coding tool use (AI_USAGE.md), cost awareness

### Takeaway
In AI-engineering take-homes, the clearest differentiator is evaluation: a small, honest eval set with retrieval and generation measured separately, error analysis done by reading outputs, and any LLM judge checked against human labels. On AI tool use, companies that allow it (Canva, startups in the Formation write-up) grade judgment. They want to see AI used for well-defined subtasks, its output reviewed and corrected, and the candidate staying in control of the architecture. Unreviewed acceptance is the negative signal. No public template for AI_USAGE.md exists.

### Cited Findings
**AI coding tool use**
- **[EVIDENCE, company, June 2025]** Canva expects backend, ML and frontend candidates to use Copilot, Cursor or Claude in interviews. It assesses: "Do they understand when and how to leverage AI effectively?", "Can they identify and fix issues in AI-generated code?", and "Can they ensure AI-generated solutions meet production standards?" The most successful candidates "Used AI strategically for well-defined subtasks while maintaining control of the overall solution", "Critically reviewed and improved AI-generated code", and "didn't just prompt AI and accept whatever it generated." Weak candidates "lacked the judgment to guide AI effectively or identify when its suggestions were suboptimal." — [Simon Newton, Canva Engineering, 2025-06-11](https://canva.dev/blog/engineering/yes-you-can-use-ai-in-our-interviews)
- **[EVIDENCE, reported, Oct 2025]** A virtual-assistant SaaS company (unnamed) asks after submission, "How did you use AI in the challenge?" A seed-stage AI company encourages Cursor with Claude and probes AI use in later interviews. In one story, a candidate's full Cursor-generated solution was "overly complicated". The candidate rolled it back, built the base by hand, then used Cursor for targeted help. The article's thesis is "The test is not whether you used AI, but how responsibly and effectively you used it." — [Sophie Novati, Formation, 2025-10-07](https://formation.dev/blog/how-technical-interviews-are-evolving-with-ai)
- **[EVIDENCE, reported]** Shopify's Head of Engineering said remote candidates can use any tools (via The Pragmatic Engineer, July 2025). Cursor itself bans AI in its interviews (Business Insider). — [Pragmatic Engineer](https://newsletter.pragmaticengineer.com/p/the-pulse-146) (paywalled; excerpt only); [Business Insider NL](https://www.businessinsider.nl/inside-cursors-hiring-strategy-no-ai-in-interviews-and-a-2-day-project-with-the-team/)
- **[EVIDENCE, HN, 2024-2025]** Reviewers report that AI erodes take-home signal: "Anyone could nowadays finish your take home assignment in a fraction of the effort" ([HN imiric, 2024-05-01](https://news.ycombinator.com/item?id=40226236)), and "I recently fed it into ChatGPT and asked it to do the assignment. It did it perfectly" ([HN physicles, 2025-02-21](https://news.ycombinator.com/item?id=43124171)). Karat argues that take-homes degrade fastest under AI. — [Karat 2026 trends](https://karat.com/engineering-interview-trends-2026/)
- **[OPINION, vendor]** Orbyt claims that candidates who include "a brief methodology note explaining their AI usage ... consistently advance further". This is unverified vendor data. The advice is to "show where you improved the AI's output, and demonstrate thinking the AI could not do on its own." — [Orbyt](https://www.orbytjobs.ai/ai-skills/demo-ai-skills-take-home)
- **[OPINION]** For AI-use documentation, keep the prompts that "materially shaped the work" and keep "both failed and successful attempts." The AIUSE.md open spec stores per-session logs under `/aiuse/`. — [uxcel](https://app.uxcel.com/courses/ai-prompts-foundations/ethics-limitations-and-best-practices-678/documenting-ai-usage-transparently-2882); [aiusemd/spec](https://github.com/aiusemd/spec)

**RAG quality and evals**
- **[PRACTITIONER AUTHORITY]** Hamel Husain and Shreya Shankar advise: "Start with error analysis, not infrastructure." "Generic evaluations waste time and create false confidence when you use them as quality measures" and "The abuse of generic metrics is endemic" (they name helpfulness, coherence and quality scores). "Binary evaluations force clearer thinking and more consistent labeling", while "Likert scales introduce significant challenges." On LLM judges: "validate it against human labels before trusting its decisions", aiming for high TPR and TNR. "Synthetic data cannot tell you how common a failure is in production." They recommend reviewing at least 100 traces. — [Hamel Husain & Shreya Shankar, Evals FAQ (updated Sept 2026)](https://hamel.dev/blog/posts/evals-faq/)
- **[OPINION, interview-prep, unsourced]** Applied-AI interviews treat evals as the main differentiator: "Teams building this for real spend more time on evaluation than on the pipeline." Asked whether you'd trust an LLM judge before checking it against humans, "The right answer is no." The article also says to report recall@k, precision@k and MRR/NDCG, because "if you can't give 'good retrieval' a number, the interviewer assumes you tuned by feel." It expects prompt regression testing ("replaying failed runs against a fixed set of cases before shipping a prompt change"), cost and latency reasoning with real numbers, and a deterministic check that "the cited source even exists". It cites no primary evidence. — [techinterview.org, updated 2026-07-23](https://www.techinterview.org/post/3233476824/ai-engineer-interview-rag-agents-evals/)
- **[OPINION]** For RAG take-homes, "Even 10 manual examples is fine; zero is a signal." A 4-hour submission with 12 files and a custom logging framework "signals poor judgment about scope." One common take-home format is "Build a Q&A system, an eval set of 20 questions, and report recall@5 and faithfulness." — search snippets from [techinterview.org](https://www.techinterview.org/post/3233476824/ai-engineer-interview-rag-agents-evals/) and [dev.to/rishi_kora](https://dev.to/rishi_kora/ace-the-ai-engineer-take-home-what-2026-hiring-teams-score-1j6k) (not fetched in full)
- **[OPINION]** Measure retrieval relevance and generation groundedness as separate rubrics, because averaging them "hides which layer failed". Groundedness is checked claim by claim against retrieved spans. Evaluating with oracle context and with retrieved context isolates retrieval failures from generation failures. Criteria include citation precision and recall, and abstention. — [futureagi](https://futureagi.com/blog/rag-evaluation-metrics-deep-dive-2026/); [kili-technology](https://kili-technology.com/blog/rag-evaluation-guide-measuring-retrieval-and-generation-as-separate-problems)
- **[OPINION]** A senior AI-engineer rubric scores five areas from 1 to 5: LLM integration, RAG design, vector DB choice, evaluation design, and production monitoring. — [boundev.ai](https://www.boundev.ai/blog/how-to-evaluate-senior-ai-engineer) (snippet)

### Inferences
- **AI_USAGE.md:** no template exists, so a strong one should mirror what Canva grades. Organize it by decision rather than by tool. For each phase (design, scaffolding, tests, prompts, debugging), state what was delegated, what was rejected or rewritten and why (concrete cases where the AI was wrong), and how output was verified (tests, evals, review). Include a short "where AI was wrong" section. It is the most direct evidence of the "identify and fix issues in AI-generated code" criterion. A log of raw prompts is weaker than a curated set of decisions.
- **Evals:** a small, honest, reproducible eval is likely to beat a large framework. Examples: 20-50 labelled questions, recall@k reported for retrieval, citation validity as a deterministic check, binary pass/fail for groundedness, and a stated judge-vs-human agreement. Each number should ship with the `make` command that regenerates it. Generic faithfulness or "quality" scores from an off-the-shelf library with no calibration may be read as a negative signal by reviewers who know the Hamel/Shankar line.
- **Prompt engineering:** versioned prompt files, a changelog tied to eval deltas, and a strict output schema all show prompt engineering as engineering. Clever prompt prose doesn't.
- **Cost awareness:** report per-request token and $ figures and per-ingest cost for "1000s of documents". Model routing by role is a concrete talking point. The source for this claim is opinion-grade.
- Because AI has weakened the take-home's signal (HN, Karat), reviewers will likely lean harder on things AI can't fake: coherent tradeoff reasoning, a live walkthrough, and evidence of judgment. Any deviation should be explained in the submission.

### Gaps
- No primary source from an AI company (OpenAI, Anthropic, Cohere, etc.) publishes its RAG take-home rubric. One secondhand source says Anthropic asks for take-homes without Claude unless allowed. This is unverified, and [techinterview.org](https://www.techinterview.org/post/3233474911/ai-tool-policy-tracker-tech-companies-2026/) is the only source.
- I couldn't find a real AI_USAGE.md from a praised take-home submission. The only one found is a university lab file ([wits.ac.za](https://sdp.ms.wits.ac.za/Mpho10111/coms3011a-lab1/src/branch/main/docs/AI_USAGE.md)).
- I couldn't get the full text of the Pragmatic Engineer pieces (paywalled).

## 3. Common failure modes: over-engineering, unfinished features, unrunnable, missing demo, weak docs, ignoring spec, excessive scope

### Takeaway
The documented rejection reasons are: it doesn't run or can't be set up; sloppy handling of basic edge cases; missing or weak documentation; and over-engineering relative to the stated scope. AI-generated over-complexity is a newer variant of the last one. Strictness varies by reviewer: some reject for lint errors and missing tests, others for "over-engineering". The safe zone is a finished core that is tested and runs cleanly, with deliberate scope cuts written down.

### Cited Findings
- **[EVIDENCE, 2019]** Not runnable, or unclear how to run, is the first gate ("Does it work at all?"). Sloppiness is "one of the most common reasons" for rejecting working submissions, and missing documentation affects "80% of submissions". — [Orosz](https://dev.to/gergelyorosz/9-insider-tips-to-ace-your-next-takehome-project-for-frontend-fullstack-and-mobile-interviews-41nn)
- **[EVIDENCE, candidate reports]** A candidate's Expensify submission was "rejected because I was told that my solution was over-engineered". Another candidate was "rejected for pedantic reasons like linting errors, not having tests." — [Glassdoor Expensify](https://www.glassdoor.ca/Interview/Expensify-com-Interview-Questions-E254791.htm) (via search snippet)
- **[EVIDENCE, reported, 2025]** An AI-generated full solution came out "overly complicated", and the candidate succeeded by rolling it back and building a simpler base. — [Formation](https://formation.dev/blog/how-technical-interviews-are-evolving-with-ai)
- **[EVIDENCE, reported]** Reviewers favored polish and completeness ("keyboard shortcuts for everything") over ambitious design documents. — [HN saagarjha](https://news.ycombinator.com/item?id=43982643)
- **[OPINION]** "it is important that you do not over engineer your application because it sends the wrong message to potential employers." — [thisdot labs](https://labs.thisdot.co/blog/how-to-avoid-common-pitfalls-and-ace-your-take-home-assignment) (snippet)
- **[OPINION]** Heavy framework dependence ("hiding behind libraries") obscures the candidate's skill. In RAG this means a LangChain/LlamaIndex one-liner pipeline. — [Orosz](https://dev.to/gergelyorosz/9-insider-tips-to-ace-your-next-takehome-project-for-frontend-fullstack-and-mobile-interviews-41nn)
- **[EVIDENCE, survey]** Assignments routinely take 5x the stated time, so time-boxing and documenting what was cut is the norm. — [interviewing.io](https://interviewing.io/blog/why-engineers-dont-like-take-homes-and-how-companies-can-fix-them)

### Inferences
- **Over-engineering risk for this project:** the Vault design (hash-partitioned chunks, convergence engine, multi-role model routing, otel-lgtm stack, Celery, reranker service, Locust) is ambitious. The evidence suggests reviewers penalize machinery that isn't justified by the spec or isn't finished. The spec's "think about scale: 1000s of documents" licenses scale design, but 1000s of documents is modest. Partitioning and heavy infra may read as gold-plating unless the README justifies each piece with a measured number or a one-line reason, and marks it optional. Everything the reviewer must run should start with one command.
- The highest-risk failure mode is a broad feature list with half-working features. The spec explicitly prefers the opposite.
- A reviewer who can't run the stack quickly (Docker pulls, API keys, migrations) may never see the AI quality. A seeded demo dataset plus a recorded demo hedges against that.

### Gaps
- No quantitative data exists on how often each failure mode causes rejection. All evidence is anecdotal or comes from a single reviewer.
- Several snippets (8 anti-patterns post, Glassdoor) couldn't be fetched in full. One returned a 404.

## 4. Exemplary public submissions / praised open-source chat-with-docs projects

### Takeaway
I could not find any publicly praised take-home submissions for RAG. Well-regarded open-source chat-with-docs projects show the presentation bar: a live demo or preview, citations with source highlighting, and a docker quickstart. Small portfolio repos with RAGAS tables exist but have no evidence of reviewer praise.

### Cited Findings
- **[EVIDENCE, repo]** Kotaemon (Cinnamon) has about 25.9k GitHub stars. Its README has static preview images (no GIF), two live Hugging Face Spaces demos plus a Colab notebook, and "Advanced citations with document preview", which shows citations highlighted in an in-browser PDF viewer. Its Docker quickstart is a multi-line `docker run`, and it has no system architecture diagram. — [github.com/Cinnamon/kotaemon](https://github.com/Cinnamon/kotaemon)
- **[UNVERIFIED portfolio repos]** These partially match the stack (FastAPI, pgvector, citations, RAGAS tables), but there is no evidence anyone praised them:
  - [pm32900/personal-knowledge-vault](https://gh.r-mj.com/pm32900/personal-knowledge-vault): FastAPI, pgvector, cited answers
  - [airelcamilo/code-compass](https://gh.r-mj.com/airelcamilo/code-compass): RAGAS mean/median table
  - [melbinjp/citerag](https://github.com/melbinjp/citerag): FastAPI, page-level citations
  - [LvR33/RAGMeUp](https://github.com/LvR33/RAGMeUp): pgvector, Ragas script
  - Source: search snippets only.
- **[OPINION, blog]** A dev.to post describes a RAG system that "enforces its own citations and blocks its own merges", using CI gates on eval results. — [dev.to/vijaya_bollu](https://dev.to/vijaya_bollu/-i-built-a-rag-system-that-enforces-its-own-citations-and-blocks-its-own-merges-16ad) (snippet; not fetched)

### Inferences
- The visible quality bar from popular projects is a demo you can see without installing, citations a user can click through to a highlighted source span, and a single quickstart command. For a backend-only take-home, the equivalent is a short video showing an answer whose citation resolves to exact stored text, plus a scripted `make demo`/seed path.
- An eval results table with a regenerate command would differentiate the submission. It is uncommon among popular projects (Kotaemon has none).

### Gaps
- I found no hiring-manager post praising a specific public RAG take-home submission. No "hall of fame" or reviewed-example corpus exists in what I searched.

## 5. README structure, demo format/length, and reviewer time

### Takeaway
Reviewers spend minutes, not hours, on a first pass: 5-20 minutes per Orosz, and an unsourced 3-15 minutes per blogs. Less time goes to submissions that work and are clear. Demo videos are usually capped at 5 minutes, with 2-4 minutes recommended. The README should put "run it" and "what works" in the first screen.

### Cited Findings
- **[EVIDENCE, 2019]** "I spend 5-20 minutes on each one, spending less time on ones that work well and are clear." — [Orosz](https://dev.to/gergelyorosz/9-insider-tips-to-ace-your-next-takehome-project-for-frontend-fullstack-and-mobile-interviews-41nn)
- **[OPINION, conflicting]** One blog says hiring managers spend 3-8 minutes on a first pass. Another says 10-15 minutes. Neither cites data, and these came via aggregated search snippets from career blogs I couldn't attribute precisely. — [tech-in-view](https://tech-in-view.vercel.app/blog/software-engineer-take-home-assignment-tips); [prachub](https://prachub.com/resources/software-engineer-take-home-assignment-guide-2026-scope-tests-and-submission)
- **[EVIDENCE, published submission rules]** Demo caps are usually at most 5 minutes:
  - medblocks FHIR challenge: "maximum 5 minutes" ([medblocks](https://medblocks.com/training/courses/fhir-app-challenge/41-submission))
  - Pulp project: "All videos must be under 5 minutes. The ideal demo length being about 3 - 4 minutes" ([pulp.plan.io](https://pulp.plan.io/projects/pulp/wiki/Demo_Presenter_Notes/10/annotate))
  - a designer take-home: "record a 5 minute video" ([Cognito task](https://amadeuscarnegie.notion.site/Cognito-UI-UX-Designer-Assessment-Task-2e6cf5cb88e14c6f8d838953cbb7b596))
- **[OPINION]** "For walkthrough or demo videos we find 2-3 minutes to be ideal. Around 5 minutes or more you will risk losing people's attention." — [navattic](https://www.navattic.com/blog/should-i-use-Loom-to-send-demos-to-prospects) (snippet)
- **[CLAIMED]** Loom's free tier limits recordings to 5 minutes, which makes 5 minutes a de facto cap. — [nemovideo Loom review](https://www.nemovideo.com/blog/ai-video-workflows/loom-review) (snippet)
- **[EVIDENCE, 2019]** Recommended README content: setup notes for the reviewer; what was built and what was deliberately not built; design and framework decisions; areas for improvement. — [Orosz](https://dev.to/gergelyorosz/9-insider-tips-to-ace-your-next-takehome-project-for-frontend-fullstack-and-mobile-interviews-41nn)

### Inferences
Recommended README order for a 5-20 minute reviewer (synthesized from the evidence above; not itself sourced):
1. One-paragraph summary and a link to the demo video (3-5 min).
2. Quickstart: one command plus a seed, with the `.env` keys required.
3. "What works" mapped to the spec's feature list, with each item marked done or deferred.
4. Architecture diagram and request flow.
5. Key decisions and tradeoffs, with what was cut and why.
6. Eval results table, with regenerate commands and $ cost.
7. Scale notes for 1000s of documents (measured, not asserted).
8. API reference: link to OpenAPI, plus 3-4 curl examples.
9. Limitations and next steps.
10. A link to AI_USAGE.md.

Demo video, about 3-5 minutes:
- Upload and processing status (async).
- Insights.
- A cited chat answer, with a click-through to the exact source text.
- A refusal or abstention when the documents don't contain the answer.
- The metrics endpoint.
- One sentence on the eval numbers.
- Show the core flow in the first 60 seconds.

### Gaps
- I found no rigorous (survey or published) data on reviewer time for 2023-2026. The only first-hand figure, Orosz's 5-20 minutes, is from 2019.
- No source specifically compares a GIF in the README with a Loom video for take-homes. The spec asks for "a demo", so either is acceptable, and a short video plus a README GIF covers both.
