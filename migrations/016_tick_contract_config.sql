-- ============================================
-- Migration 016: HERMES-owned tick-contract governance config
-- WO: WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001
-- Database: tradingSignals (hermes_config, GOV-CFG-001)
-- Date (UTC): 2026-06-10
-- Append-only INSERT (no UPSERT). Re-apply fails loud on UNIQUE config_key.
-- No defaults in code: every tunable is governed config; missing => fail loud.
-- ============================================

INSERT INTO hermes_config (config_key, config_value, value_type, description, llm_reasoning) VALUES
('tick_contract_version', 'hermes.tick.v1', 'string',
 'Version string stamped on every HERMES tick contract row/stream entry.',
 '{"rationale": "Explicit contract version lets consumers (incl. future structure_engine ingest-consumer) detect schema and migrate; v1 is the first HERMES-owned raw-tick contract. Breaking changes bump the version and coexist during migration.", "source": "WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001"}'),

('tick_stream_enabled', 'false', 'bool',
 'Master gate for HERMES tick Redis Stream publishing. false = capability present but not active (no cutover).',
 '{"rationale": "This WO ships capability only; cutover is a later authorised step. Defaulting to false keeps the live path unchanged until R2D2 + Architect approve. Fail-loud if missing — never silently on.", "source": "WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001"}'),

('tick_stream_maxlen', '100000', 'int',
 'Approximate MAXLEN trim for hermes:ticks:<instrument> Redis Stream (XADD MAXLEN ~).',
 '{"rationale": "~100k entries per instrument bounds memory while retaining a generous replay window for a low-latency consumer; SQL tradingSignals.ticks remains the durable record. Approximate (~) trim keeps XADD O(1).", "source": "WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001"}'),

('tick_latest_ttl_seconds', '120', 'int',
 'TTL for hermes:ticks:latest:<instrument> so staleness is detectable by consumers.',
 '{"rationale": "120s matches HERMES tick staleness semantics (hermes_tick_staleness_threshold_sec). A missing/expired latest key signals stale truth; consumers must fail-loud/degrade on absence.", "source": "WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001"}'),

('tick_retention_warm_days', '30', 'int',
 'Warm-tier retention (days) for raw ticks in tradingSignals.ticks under HERMES ownership.',
 '{"rationale": "Placeholder value pending reconciliation with the value structure_engine retention.py currently enforces — MUST be reconciled before HERMES retention is enabled so cutover does not change purge behaviour (no split-brain retention). Retention/archive moves WITH write ownership (D-RET).", "source": "WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001", "warning": "reconcile_with_structure_engine_current_retention_before_enable"}'),

('tick_archive_enabled', 'false', 'bool',
 'Whether the HERMES tick retention job cold-archives rows before purge.',
 '{"rationale": "Cold archive (parquet) before purge preserves durable history. Defaults false until the archive path + format are ratified and the retention job is authorised to run. Fail-loud if missing.", "source": "WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001"}'),

('tick_archive_path', '/srv/ARCHIVE/hermes_ticks', 'string',
 'HERMES-owned cold-archive destination for purged raw ticks (parquet).',
 '{"rationale": "HERMES-owned archive location (not a proteus path) so archival ownership moves with the table. Used only when tick_archive_enabled=true.", "source": "WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001"}'),

('tick_seq_backfill_batch_size', '50000', 'int',
 'Batch size for the per-instrument seq backfill over the existing ~22M rows.',
 '{"rationale": "50k-row batches bound transaction size/lock time on the large table while keeping the backfill resumable; tuned at run time by the operator. Backfill is fail-loud + resumable + evidence-producing and never auto-runs.", "source": "WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001"}');
