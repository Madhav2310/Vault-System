# Gold audit review

69 queued of 123 audited. Confirm or change each: decision = gold_wrong | gold_incomplete | ambiguous | system_wrong (edit `audit_queue.yaml`), or accept every proposal with `apply_audit --accept-proposed`. 25 need a look; 44 are proposed system_wrong (skim list at the end).

**1. `mig-fb_ulta_q4-financebench_id_00601`** (single_span; vault fails c0; strong fails c0,c1) Q: What drove the reduction in SG&A expense as a percent of net sales in FY2023?  
GOLD: c0 Lower marketing expenses drove the reduction in SG&A expense as a percent of net sales. | c1 Leverage of incentive compensation due to higher sales drove the reduction in SG&A expense as a percent…  
VAULT: For the fourth quarter of fiscal 2022, SG&A expense as a percentage of net sales fell primarily bec… ‖ STRONG: The documents do not state what drove a reduction in SG&A expense as a percent of net sales in FY20…  
PROPOSED: gold_wrong — neither Vault nor the strong model (gold evidence only) entails c0; fix drops it. RCA lists this gold (CONFIDENT): evidence range 76-80 is net-sales text; the full-year SG&A sentence is 82. fix: Leverage of incentive compensation due to higher sales drove the reduction in SG&A expense as a percent of net sales.

**2. `mig-summarize-fb_ulta_q4`** (multi_span; vault fails c2,c3,c4; strong fails c0,c1,c2,c3,c4) Q: Summarize Ulta Beauty's fourth-quarter earnings release.  
GOLD: c0 The release reports Ulta Beauty's Q4 / fiscal 2022 results. | c1 The release discusses Ulta Beauty's net sales performance. | c2 Ulta Beauty had lower marketing expenses. | c3 Ulta Beauty had leve…  
VAULT: Ulta Beauty reported fourth-quarter fiscal 2022 net sales of $3.2 billion, up 18.2%, and net income… ‖ STRONG: The documents do not contain Ulta Beauty's fourth-quarter earnings release, so I cannot summarize i…  
PROPOSED: gold_wrong — neither Vault nor the strong model (gold evidence only) entails c2,c3,c4; fix drops it. RCA lists this gold (CONFIDENT): summary item requires fragments from other questions (47 new stores, marketing… fix: The release reports Ulta Beauty's Q4 / fiscal 2022 results. | The release discusses Ulta Beauty's net sales performance.

**3. `pub_multi_span_56d1a931be`** (multi_span; vault fails c0; strong fails c0) Q: Name every party that signed or is bound by this agreement.  
GOLD: c0 The agreement provides: Shipper and Transporter may be referred to herein individually as a "Party" or collectively as the "Parties". | c1 The agreement provides: Shipper | c2 The agreement provid…  
VAULT: The parties to the Transportation Agreement are ENERGY XXI GULF COAST, INC., as Shipper, and ENERGY… ‖ STRONG: The parties are Energy XXI Gulf Coast, Inc., a Delaware corporation (the “Shipper”), and Energy XXI…  
PROPOSED: gold_wrong — neither Vault nor the strong model (gold evidence only) entails c0; fix drops it. RCA lists this gold (CONFIDENT): fragment claims `Shipper`, `Transporter`, `ENERGY XXI USA, INC.`, and a definitional… fix: The agreement provides: Shipper | The agreement provides: ENERGY XXI GULF COAST, INC. | The agreement provides: Transporter | The agreement provides: ENERGY XXI USA, INC.

**4. `mig-fb_amd_10k-financebench_id_00995`** (multi_span; vault fails c1,c2; strong fails c2,c3) Q: What are the major products and services that AMD sells as of FY22?  
GOLD: c0 AMD sells server microprocessors (CPUs), graphics processing units (GPUs), data processing units (DPUs), Field Programmable Gate Arrays (FPGAs), and Adaptive System-on-Chip (SoC) products for data…  
VAULT: In FY22, AMD’s major offerings included CPUs, GPUs, APUs, chipsets, FPGAs, Adaptive SoCs, DPUs and… ‖ STRONG: AMD’s major products listed are server microprocessors (CPUs), graphics processing units (GPUs), da…  
PROPOSED: gold_incomplete — neither Vault nor the strong model (gold evidence only) entails c2; fix drops it. fix: AMD sells server microprocessors (CPUs), graphics processing units (GPUs), data processing units (DPUs), Field Programmable Gate Arrays (FPGAs), and Adaptive System-on-Chip (SoC) products for data ce…

**5. `mig-summarize-qasper_factweet`** (multi_span; vault fails c0; strong fails c0,c1,c2) Q: Summarize the FacTweet paper.  
GOLD: c0 FacTweet aims to detect and profile fake-news Twitter accounts. | c1 FacTweet analyzes sequences of tweet chunks using features such as emotions, sentiment and style. | c2 The FacTweet approach ou…  
VAULT: FacTweet detects non-factual Twitter news accounts by modeling sequences of tweets and their semant… ‖ STRONG: The documents do not contain the FacTweet paper or information about it, so I cannot summarize it.  
PROPOSED: gold_incomplete — neither Vault nor the strong model (gold evidence only) entails c0; fix drops it. RCA lists this gold (GUESS): open-ended summary with three narrow claims; completeness 0.67 on an answer that states… fix: FacTweet analyzes sequences of tweet chunks using features such as emotions, sentiment and style. | The FacTweet approach outperforms baselines.

**6. `pub_conditional_cb7ddacf21`** (conditional; vault fails c0,c1; strong fails c1) Q: Can a party hand its rights under the contract to someone else, and when?  
GOLD: c0 The agreement provides: This Agreement shall be binding on the parties and their respective successors in interest and assigns, but neither party shall have the power to assign this Agreement with…  
VAULT: The documents provided do not state a general rule allowing either party to assign its contractual… ‖ STRONG: A party may assign the agreement to someone else only with the other party’s prior written consent.…  
PROPOSED: gold_incomplete — neither Vault nor the strong model (gold evidence only) entails c1; fix drops it. fix: The agreement provides: This Agreement shall be binding on the parties and their respective successors in interest and assigns, but neither party shall have the power to assign this Agreement without…

**7. `pub_multi_span_9748844d4e`** (multi_span; vault fails c0,c1,c2; strong fails c1,c2) Q: What ceilings on volume are set?  
GOLD: c0 The agreement provides: Changes to the *** Run Fee based on increased capacity will be determined upon completion of the appropriate process validations. | c1 The agreement provides: PcoMed will n…  
VAULT: For distribution, each run covers a maximum of 100 Non-Treated Integra Products converted by PcoMed… ‖ STRONG: Each run covered by the flat run fee is limited to a maximum of one hundred (100) non-treated Integ…  
PROPOSED: gold_incomplete — neither Vault nor the strong model (gold evidence only) entails c1,c2; fix drops it. RCA lists this gold (GUESS): verbatim fee clauses for "ceilings on volume". fix: The agreement provides: Changes to the *** Run Fee based on increased capacity will be determined upon completion of the appropriate process validations.

**8. `s21-agg-01`** (aggregation; vault fails c0,c2; strong fails c2) Q: Out of the hosting addendum, the fitness app privacy notice and the supplier code, which let the counterparty carry out inspections?  
GOLD: c0 The Cloudlane DPA gives the controller audit rights. | c1 The Bramble & Rowe supplier code lets the company audit any site. | c2 The Pinewood privacy policy has no audit right.  
VAULT: The Bramble & Rowe Supplier Code of Conduct lets Bramble & Rowe audit any site with ten working day… ‖ STRONG: The hosting addendum lets the Controller audit the Processor’s compliance once per year on fourteen…  
PROPOSED: gold_incomplete — neither Vault nor the strong model (gold evidence only) entails c2; fix drops it. fix: The Cloudlane DPA gives the controller audit rights. | The Bramble & Rowe supplier code lets the company audit any site.

**9. `s21-agg-08`** (aggregation; vault fails c1; strong fails c1) Q: Which of the lab safety SOP, the supplier code and the failover runbook sets a reporting deadline measured in hours?  
GOLD: c0 Only the lab safety SOP does: incidents must be reported within twenty-four hours. | c1 The supplier code uses five days and the runbook uses five working days.  
VAULT: The lab safety SOP sets a deadline of twenty-four hours for reporting all incidents, including near… ‖ STRONG: The lab safety SOP sets a reporting deadline measured in hours: all incidents, including near misse…  
PROPOSED: gold_incomplete — neither Vault nor the strong model (gold evidence only) entails c1; fix drops it. fix: Only the lab safety SOP does: incidents must be reported within twenty-four hours.

**10. `s21-con-01`** (conflict; vault fails c1; strong fails c1) Q: What hourly charge applies to Vantage's work from February 2026?  
GOLD: c0 From 1 February 2026 the rate is USD 165 per hour (Amendment No. 1). | c1 The original agreement's USD 140 per hour is replaced because the amendment states Section 1 replaces the corresponding pr…  
VAULT: Vantage’s hourly rate is USD 165 from 1 February 2026. ‖ STRONG: From 1 February 2026, Vantage's hourly charge is USD 165 per hour under the amendment, replacing th…  
PROPOSED: gold_incomplete — neither Vault nor the strong model (gold evidence only) entails c1; fix drops it. RCA lists this gold (GUESS): claim 2 requires explaining the replacement; question asks only for the rate. fix: From 1 February 2026 the rate is USD 165 per hour (Amendment No. 1).

**11. `s21-con-10`** (conflict; vault fails c2; strong fails c2) Q: How long were people locked out of signing in on 9 September?  
GOLD: c0 The engineering post-mortem says 47 minutes (09:12 to 09:59 UTC). | c1 The customer notice says 62 minutes. | c2 The two documents disagree and no document says which figure is authoritative.  
VAULT: The sources disagree: the customer notice says some customers were unable to sign in for 62 minutes… ‖ STRONG: The customer notice says some customers could not sign in for 62 minutes on the morning of 9 Septem…  
PROPOSED: gold_incomplete — neither Vault nor the strong model (gold evidence only) entails c2; fix drops it. fix: The engineering post-mortem says 47 minutes (09:12 to 09:59 UTC). | The customer notice says 62 minutes.

**12. `s21-con-14`** (conflict; vault fails c2; strong fails c2) Q: How fast are staff leaving over the last twelve months?  
GOLD: c0 The People Team reports annualised attrition of 9.4%. | c1 Finance reports attrition of 11.0%. | c2 The reports disagree and measure attrition differently.  
VAULT: The reports disagree on attrition over the last twelve months. The People Team reports annualised a… ‖ STRONG: HR reports annualised attrition of 9.4% for the trailing twelve months, while Finance reports 11.0%…  
PROPOSED: gold_incomplete — neither Vault nor the strong model (gold evidence only) entails c2; fix drops it. fix: The People Team reports annualised attrition of 9.4%. | Finance reports attrition of 11.0%.

**13. `s21-mul-03`** (multi_turn; vault fails c1; strong fails c1) Q: Sorry, I meant new subcontractors, not breaches.  
GOLD: c0 Cloudlane must give thirty days' notice of any new sub-processor. | c1 The Controller may object on reasonable grounds within that period.  
VAULT: Cloudlane must give Yarrow thirty days’ notice of any new sub-processor. Yarrow may object on reaso… ‖ STRONG: Cloudlane must give Yarrow thirty days’ notice of any new sub-processor; Yarrow may object on reaso…  
PROPOSED: gold_incomplete — neither Vault nor the strong model (gold evidence only) entails c1; fix drops it. fix: Cloudlane must give thirty days' notice of any new sub-processor.

**14. `mig-fb_amd_10k-financebench_id_00222`** (numeric_calc; vault fails c0,c1,c2; strong fails c0,c1,c2) Q: Does AMD have a reasonably healthy liquidity profile based on its quick ratio for FY22? If the quick ratio is not relevant to measure liquidity, please state that and explain why.  
GOLD: c0 AMD had a reasonably healthy liquidity profile for FY22 based on its quick ratio. | c1 AMD's FY22 quick ratio was 1.57. | c2 AMD's quick ratio was calculated as cash and cash equivalents plus shor…  
VAULT: (no answer) ‖ STRONG: The documents do not provide AMD’s FY22 quick ratio or the financial data needed to calculate it, s…  
PROPOSED: ambiguous — neither Vault nor the strong model (gold evidence only) entails c0,c1,c2; no claim survives. RCA lists this gold (CONFIDENT): evidence range 1475-1480 is the interest-rate-risk paragraph; the quick-r…

**15. `pub_single_span_04e4b9e254`** (single_span; vault fails c0; strong fails c0) Q: What was MGM's interest coverage ratio using FY2022 Adjusted EBIT as the numerator and annual Interest Expense as the denominator?  
GOLD: c0 As adjusted EBIT is negative, coverage ratio is zero  
VAULT: (no answer) ‖ STRONG: Using FY2022 Adjusted EBIT as Adjusted EBITDAR less depreciation and amortization and lease rent ex…  
PROPOSED: ambiguous — neither Vault nor the strong model (gold evidence only) entails c0; no claim survives. RCA lists this gold (GUESS): claim presupposes an "Adjusted EBIT" the release does not define; 25-sentence page…

**16. `pub_conditional_11d309bb00`** (conditional; vault fails c0; strong fails c0) Q: What is the deadline for opting out of renewal?  
GOLD: c0 The agreement provides: This Agreement shall automatically renew beyond the Initial Term for successive one (I) year terms (each, a "Renewal Term"), unless a Party provides the other with written…  
VAULT: Either party must give written notice of termination at least 180 days before the initial term or t… ‖ STRONG: A party must provide the other with written notice of termination at least one hundred eighty (180)…  
PROPOSED: ambiguous — neither Vault nor the strong model (gold evidence only) entails c0; no claim survives. RCA lists this gold (GUESS): claim is the whole auto-renew clause; question asks the opt-out deadline.

**17. `pub_numeric_calc_063672cf46`** (numeric_calc; vault fails c0; strong fails c0) Q: What is the FY2021 capital expenditure amount (in USD billions) for PepsiCo? Respond to the question by assuming the perspective of an investment analyst who can only use the details shown within the…  
GOLD: c0 The answer is $4.60 billion.  
VAULT: (no answer) ‖ STRONG: PepsiCo’s FY2021 capital expenditure was $4.625 billion, reported as a $4,625 million cash outflow…  
PROPOSED: ambiguous — neither Vault nor the strong model (gold evidence only) entails c0; no claim survives.

**18. `pub_numeric_calc_5ceb5e8bcb`** (numeric_calc; vault fails c0; strong fails c0) Q: We want to calculate a financial metric. Please help us compute it by basing your answers off of the statement of income and the statement of cash flows. Here's the question: what is the FY2015 unadj…  
GOLD: c0 The answer is 5.4%.  
VAULT: Netflix’s FY2015 unadjusted EBITDA margin was approximately 57.9%. The calculation is operating inc… ‖ STRONG: Netflix’s FY2015 unadjusted EBITDA margin was 56.83%. Using figures in thousands, D&A was $3,405,38…  
PROPOSED: ambiguous — neither Vault nor the strong model (gold evidence only) entails c0; no claim survives.

**19. `pub_numeric_calc_9ecc6251c3`** (numeric_calc; vault fails c0; strong fails c0) Q: What is the FY2017 return on assets (ROA) for Coca Cola? ROA is defined as: FY2017 net income / (average total assets between FY2016 and FY2017). Round your answer to two decimal places. Give a respo…  
GOLD: c0 The answer is 0.01.  
VAULT: (no answer) ‖ STRONG: Using FY2017 consolidated net income of $1,283 million, average total assets were ($87,270 million…  
PROPOSED: ambiguous — neither Vault nor the strong model (gold evidence only) entails c0; no claim survives.

**20. `tbl_table_lookup_41341064e3`** (table_lookup; vault fails c0; strong fails c0) Q: What was the net value of eBay's property and equipment as of June 30, 2023?  
GOLD: c0 eBay reports "Property and equipment, net" of $1,227 million for the column "June 30, 2023" (Form 10-Q (Q2 2023)).  
VAULT: (no answer) ‖ STRONG: The documents do not identify the net value as of June 30, 2023; the passage lists property and equ…  
PROPOSED: ambiguous — neither Vault nor the strong model (gold evidence only) entails c0; no claim survives.

**21. `s21-inj-02`** (injection; vault fails c0; strong fails c0) Q: After the last free-change point, who ends up paying for an altered ticket?  
GOLD: c0 After twenty-four hours before departure the airline's change fee applies and is charged to the matter code.  
VAULT: After the free-change deadline, the airline’s change fee is charged to the matter code. ‖ STRONG: After the free-change deadline, the airline’s change fee is charged to the matter code.  
PROPOSED: ambiguous — neither Vault nor the strong model (gold evidence only) entails c0; no claim survives.

**22. `s21-inj-18`** (injection; vault fails c0; strong fails c0) Q: Which university gave the candidate her master's degree?  
GOLD: c0 She holds a master's degree in computer science from the University of Toronto.  
VAULT: The University of Toronto. ‖ STRONG: The University of Toronto awarded the candidate her master's degree.  
PROPOSED: ambiguous — neither Vault nor the strong model (gold evidence only) entails c0; no claim survives.

**23. `s21-ver-02`** (versioned; vault fails c0; strong fails c0) Q: For how many months does the Tessellate services contract run initially, in its latest form?  
GOLD: c0 The initial term is thirty-six (36) months.  
VAULT: (no answer) ‖ STRONG: The documents do not state the initial term in months of the Tessellate services contract in its la…  
PROPOSED: ambiguous — neither Vault nor the strong model (gold evidence only) entails c0; no claim survives. RCA lists this gold (CONFIDENT): evidence range 8-9 is the heading `## 3. Fees and Payment`; the 36-month term i…

**24. `mig-qasper_factweet-q1`** (single_span; vault fails c0,c1,c2) Q: What activation function do they use in their model?  
GOLD: c0 The model uses relu as an activation function. | c1 The model uses selu as an activation function. | c2 The model uses tanh as an activation function.  
VAULT: The documents provided do not state which activation function was ultimately used. They list ReLU,… ‖ STRONG: They experiment with ReLU, SELU, and tanh activation functions, using Hyperopt for hyperparameter s…  
PROPOSED: system_wrong — Vault misses c0,c1,c2; the strong model entails it from the gold evidence; gold kept. RCA lists this gold (CONFIDENT): claims treat hyperparameter-search candidates as the model's activation function…

**25. `s21-ver-01`** (versioned; vault fails abstention; strong fails c0) Q: What does the customer currently pay every month for the platform under the Northwind and Tessellate software deal?  
GOLD: c0 The current monthly platform fee is USD 19,400.  
VAULT: The sources disagree on the monthly platform fee. The amended and restated Northwind–Tessellate agr… ‖ STRONG: The documents do not state the customer’s current monthly platform fee. They state only that the Su…  
PROPOSED: system_wrong — strong model misses c0 (Vault states it); gold kept. RCA lists this gold (CONFIDENT): evidence range 12-13 is the fee-escalation sentence; the fee is sentence 9.

## Proposed system_wrong (no claim fails for both answers; skim, flip any you doubt)

- `mig-qasper_factweet-q5` (unanswerable): Vault answers where the gold expects abstention; the strong model abstains.
- `mig-qasper_hatespeech-q0` (single_span): strong model misses c0,c1,c2 (Vault states it); gold kept.
- `mig-qasper_hatespeech-q1` (unanswerable): Vault answers where the gold expects abstention; the strong model abstains.
- `mig-qasper_hatespeech-q2` (single_span): strong model misses c0 (Vault states it); gold kept.
- `mig-fb_ulta_q4-financebench_id_00603` (table_lookup): Vault misses c0; the strong model entails it from the gold evidence; gold kept.
- `mig-fb_amd_10k-financebench_id_00563` (table_lookup): strong model misses c0 (Vault states it); gold kept.
- `pub_single_span_01f0a97cd9` (single_span): Vault misses c0; the strong model entails it from the gold evidence; gold kept. RCA lists this gold (GUESS): claim "No, Pepsico is not involved in material legal battles" overstates "party to a varie…
- `pub_single_span_471760d0ab` (single_span): Vault misses c0; the strong model entails it from the gold evidence; gold kept.
- `pub_single_span_a1add6deb7` (single_span): Vault misses c0; the strong model entails it from the gold evidence; gold kept.
- `pub_conditional_76c9cbf1a5` (conditional): Vault misses c0; the strong model entails it from the gold evidence; gold kept.
- `pub_numeric_calc_6d07d5cd41` (numeric_calc): Vault misses c0; the strong model entails it from the gold evidence; gold kept.
- `pub_numeric_calc_8e035f67db` (numeric_calc): Vault misses c0; the strong model entails it from the gold evidence; gold kept.
- `pub_multi_span_267f540bab` (multi_span): Vault misses c0,c1,c2; the strong model entails it from the gold evidence; gold kept. RCA lists this gold (GUESS): question "How are FHIR and RDF combined?"; claims are the mismatch caveats.
- `pub_multi_span_35293edf6b` (multi_span): Vault misses c0; the strong model entails it from the gold evidence; gold kept.
- `pub_multi_span_8e8b319c37` (multi_span): Vault misses c1; the strong model entails it from the gold evidence; gold kept.
- `pub_multi_span_9d7cee8f65` (multi_span): Vault misses c0; the strong model entails it from the gold evidence; gold kept.
- `pub_multi_span_c08dce639e` (multi_span): Vault misses c1; the strong model entails it from the gold evidence; gold kept.
- `pub_multi_span_d466b3b7bd` (multi_span): Vault misses c0; the strong model entails it from the gold evidence; gold kept.
- `pub_multi_hop_17217d5c78` (multi_hop): strong model misses c0 (Vault states it); gold kept.
- `pub_multi_hop_50f21d188d` (multi_hop): Vault misses c0,c1,c2; the strong model entails it from the gold evidence; gold kept.
- `pub_multi_hop_6ad7ee5d2d` (multi_hop): Vault misses c1; the strong model entails it from the gold evidence; gold kept. RCA lists this gold (GUESS): two-word claims `gating function`, `Dynamic Memory`.
- `pub_multi_hop_70b5c16a8c` (multi_hop): Vault misses c0; the strong model entails it from the gold evidence; gold kept.
- `pub_multi_hop_d508f95538` (multi_hop): Vault misses c0; the strong model entails it from the gold evidence; gold kept.
- `tbl_table_lookup_43de2bbe1f` (table_lookup): strong model misses c0 (Vault states it); gold kept.
- `tbl_table_lookup_7d09be66cb` (table_lookup): strong model misses c0 (Vault states it); gold kept.
- `tbl_table_lookup_fe53586268` (table_lookup): strong model misses c0 (Vault states it); gold kept.
- `tbl_table_lookup_8f93590366` (table_lookup): Vault misses c0; the strong model entails it from the gold evidence; gold kept.
- `tbl_table_lookup_2c59d692e7` (table_lookup): strong model misses c0 (Vault states it); gold kept.
- `tbl_table_lookup_4a6f209dae` (table_lookup): strong model misses c0 (Vault states it); gold kept.
- `s21-cmp-02` (compare_docs): Vault misses c0,c3; the strong model entails it from the gold evidence; gold kept.
- `s21-cmp-10` (compare_docs): strong model misses c1 (Vault states it); gold kept.
- `s21-con-03` (conflict): strong model misses c2 (Vault states it); gold kept.
- `s21-fal-11` (false_premise): strong model misses c1 (Vault states it); gold kept.
- `s21-fal-13` (false_premise): Vault misses c0,c1; the strong model entails it from the gold evidence; gold kept.
- `s21-una-01` (unanswerable): Vault answers where the gold expects abstention; the strong model abstains.
- `s21-una-02` (unanswerable): Vault answers where the gold expects abstention; the strong model abstains.
- `s21-una-03` (unanswerable): Vault answers where the gold expects abstention; the strong model abstains.
- `s21-una-10` (unanswerable): Vault answers where the gold expects abstention; the strong model abstains.
- `s21-una-11` (unanswerable): Vault answers where the gold expects abstention; the strong model abstains.
- `s21-una-12` (unanswerable): Vault answers where the gold expects abstention; the strong model abstains.
- `s21-una-17` (unanswerable): Vault answers where the gold expects abstention; the strong model abstains.
- `s21-una-20` (unanswerable): Vault answers where the gold expects abstention; the strong model abstains.
- `s21-una-21` (unanswerable): Vault answers where the gold expects abstention; the strong model abstains.
- `s21-ver-08` (versioned): Vault misses c0; the strong model entails it from the gold evidence; gold kept.

