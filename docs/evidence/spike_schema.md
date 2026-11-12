# S0.20 spike — partitioned halfvec + composite GIN

Date: 2026-11-12 · Postgres 16.15 · pgvector 0.8.7 · scratch DB `vault_spike` (100 owners × 500 chunks + one 21k-chunk owner).

Reproduce: `scripts/spikes/run_schema_spike.sh ddl && scripts/spikes/run_schema_spike.sh explain`

## EXPLAIN output (50k-row seed)
```
                                   QUERY PLAN                                    
---------------------------------------------------------------------------------
 Limit
   InitPlan 1 (returns $0)
     ->  Seq Scan on q
   ->  Sort
         Sort Key: ((chunks.embedding <=> $0))
         ->  Seq Scan on chunks_p13 chunks
               Filter: (owner_id = '003beb5c-5c26-4f13-a84d-d8464976ae15'::uuid)
(7 rows)

                                     QUERY PLAN                                      
-------------------------------------------------------------------------------------
 Limit
   ->  Bitmap Heap Scan on chunks_p13 chunks
         Recheck Cond: (owner_id = '003beb5c-5c26-4f13-a84d-d8464976ae15'::uuid)
         Filter: (tsv_en @@ '''indemnif'''::tsquery)
         ->  Bitmap Index Scan on chunks_p13_pkey
               Index Cond: (owner_id = '003beb5c-5c26-4f13-a84d-d8464976ae15'::uuid)
(6 rows)

                                       QUERY PLAN                                       
----------------------------------------------------------------------------------------
 Limit (actual time=11.379..11.384 rows=40 loops=1)
   InitPlan 1 (returns $0)
     ->  Seq Scan on q (actual time=0.003..0.004 rows=1 loops=1)
   ->  Sort (actual time=11.378..11.380 rows=40 loops=1)
         Sort Key: ((chunks.embedding <=> $0))
         Sort Method: top-N heapsort  Memory: 29kB
         ->  Seq Scan on chunks_p13 chunks (actual time=0.068..11.163 rows=500 loops=1)
               Filter: (owner_id = '003beb5c-5c26-4f13-a84d-d8464976ae15'::uuid)
               Rows Removed by Filter: 1000
 Planning Time: 0.055 ms
 Execution Time: 11.403 ms
(11 rows)

```

## Findings
1. **DDL works (AC1):** 32 hash partitions, `halfvec(1536)`, STORED generated `tsv_en`, composite GIN `(owner_id, tsv_en)` via `btree_gin`. The GIN index propagates to all 32 partitions.
2. **Pruning works (AC2):** a scoped exact vector query touches exactly one partition (`chunks_p13`). Exact top-40 over 500 rows: **8.9 ms**.
3. **The composite GIN is not chosen at spike scale (AC3 not met as written).**
   - With `enable_seqscan=off`, the planner uses a bitmap scan on the **PK btree** `(owner_id, id)` and filters `tsv_en`.
   - Unaided, on a 21k-chunk owner with short texts, it seq-scans: 2.7 ms for a rare term, 2.6 ms for a common one.
   - Spike texts are about 6 words. Real chunks run about 300–500 tokens, so tsvector size and the scan cost per row differ by roughly 50×.
   - **Decision deferred to S2.20** (simulated scale DB): measure the lexical p95 with and without the GIN at realistic text sizes. Drop the GIN if the planner never uses it (write amplification for nothing).
