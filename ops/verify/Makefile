# WO-TRADING-SIGNALS-UTCNOW-CLEANUP-0001 — tradingSignals (hermes) verification harness.
#
# Repeatable proof commands per architect closure-compliance bar. Mirrors the
# helios_v3/ops/verify pattern. Each target prints PASS/FAIL.
#
# Usage:
#   cd /srv-dev/tradingSignals
#   make -f ops/verify/Makefile help
#   make -f ops/verify/Makefile verify-all

REPO_ROOT := $(shell cd $(dir $(lastword $(MAKEFILE_LIST)))/../.. && pwd)
PYTHON := /usr/bin/python3
TS := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)

.PHONY: help verify-all \
        test-utc-compliance \
        verify-trading-signals-utc-clean \
        verify-trading-signals-runtime \
        verify-trading-signals-canary \
        verify-local-main-reconciliation

help:
	@echo "tradingSignals verification harness — WO-TRADING-SIGNALS-UTCNOW-CLEANUP-0001"
	@echo ""
	@echo "Targets:"
	@echo "  make verify-all                          — run every check"
	@echo "  make test-utc-compliance                 — banned-token scan + truth invariants"
	@echo "  make verify-trading-signals-utc-clean    — alias for compliance test"
	@echo "  make verify-trading-signals-runtime      — signal-service-dev active + canary GREEN"
	@echo "  make verify-trading-signals-canary       — hermes-canary still GREEN post-cleanup"
	@echo "  make verify-local-main-reconciliation    — local main == origin/main and no operator overlay"

verify-all: \
	test-utc-compliance \
	verify-trading-signals-utc-clean \
	verify-trading-signals-runtime \
	verify-trading-signals-canary \
	verify-local-main-reconciliation
	@echo ""
	@echo "============================================================"
	@echo "verify-all PASS — all tradingSignals UTC closure checks green ($(TS))"
	@echo "============================================================"

# --- Tests ------------------------------------------------------------------

test-utc-compliance:
	@echo "[$(TS)] test-utc-compliance"
	@cd $(REPO_ROOT) && $(PYTHON) -m unittest tests.test_utc_compliance > /tmp/.ts_utc_test.out 2>&1 || \
	    (cat /tmp/.ts_utc_test.out; echo "FAIL: utc compliance test"; exit 1)
	@grep -E "Ran [0-9]+ tests|^OK$$" /tmp/.ts_utc_test.out
	@echo "PASS: utc compliance test"

verify-trading-signals-utc-clean: test-utc-compliance
	@# alias

# --- Runtime checks ---------------------------------------------------------

verify-trading-signals-runtime:
	@bash $(REPO_ROOT)/ops/verify/check_runtime.sh

verify-trading-signals-canary:
	@bash $(REPO_ROOT)/ops/verify/check_canary.sh

verify-local-main-reconciliation:
	@bash $(REPO_ROOT)/ops/verify/check_local_main.sh
