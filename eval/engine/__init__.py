"""Eval engine core (plan S2.14): stage cache, Batch mode, spend ledger, resumable run state.

Public surface: `Engine`, `Stage`, `StageKey`, `Suite`, `RunConfig` (runner); `StageCache` (cache);
`BatchBackend`, `FakeBatchBackend`, `OpenAIBatchBackend` (batch); `SpendLedger` (ledger); `RunDir` (state).
"""
