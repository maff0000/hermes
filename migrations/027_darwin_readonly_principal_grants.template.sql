-- ============================================
-- DARWIN read-only principal — SELECT-only grants over the canonical historical surface
-- WO: WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-AUTHORITY-0001, Phase 8
-- Database: tradingSignals
--
-- TEMPLATE ONLY — never committed with a real password. Replace <DARWIN_RO_PASSWORD> with a freshly
-- generated secret at execution time; the executed password is a runtime secret and lives OUTSIDE this
-- repository (see the WO's final report for the credential's storage location, never its value).
--
-- Grants SELECT ONLY on the 7 canonical objects from migration 027 (4 views + 2 tables + 1 unified view).
-- Explicitly NO privilege on any other table/view in this schema (candles_M1/M5/M15/H1/H4/D1, ticks_*,
-- hermes_*, or anything else) — MariaDB's view privilege model means SELECT on a view is sufficient for an
-- invoker with no rights on the view's underlying base tables (views here use the default DEFINER security,
-- created by the schema owner), so this genuinely scopes DARWIN to the 6-timeframe canonical surface only.
-- ============================================

CREATE USER IF NOT EXISTS 'darwin_ro'@'%' IDENTIFIED BY '<DARWIN_RO_PASSWORD>';

GRANT SELECT ON tradingSignals.canonical_candles       TO 'darwin_ro'@'%';
GRANT SELECT ON tradingSignals.canonical_candles_m1    TO 'darwin_ro'@'%';
GRANT SELECT ON tradingSignals.canonical_candles_m5    TO 'darwin_ro'@'%';
GRANT SELECT ON tradingSignals.canonical_candles_m15   TO 'darwin_ro'@'%';
GRANT SELECT ON tradingSignals.canonical_candles_h1    TO 'darwin_ro'@'%';
GRANT SELECT ON tradingSignals.canonical_candles_h4    TO 'darwin_ro'@'%';
GRANT SELECT ON tradingSignals.canonical_candles_d1    TO 'darwin_ro'@'%';

FLUSH PRIVILEGES;

-- Verification (run after applying): SHOW GRANTS FOR 'darwin_ro'@'%';
-- Expected: exactly the 7 SELECT grants above — nothing else, no INSERT/UPDATE/DELETE/CREATE/ALTER/DROP,
-- no grant on any other schema object.
