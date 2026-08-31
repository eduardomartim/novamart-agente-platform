# Local commands, identical to the ones CI runs.
#
# This file earns its place for one reason: the marker expression. The 72-call
# incident happened because someone typed `pytest -m "not docker"`, and a `-m`
# on the command line replaces the one in pyproject.toml rather than combining
# with it -- so `not live` silently stopped applying. Every target below spells
# out the safe expression, so the way to run the suite is also the way that
# cannot select live tests by accident.
#
# Nothing here wraps a command to make it shorter. Each target is the real
# command, written once, in the place both a person and CI read it from.

PYTHON ?= python
OFFLINE_MARKERS := not live and not docker

.PHONY: help lint typecheck test security manifests container audit gates live-gate protected

help:
	@echo "make lint       ruff"
	@echo "make typecheck  mypy strict"
	@echo "make test       the offline suite"
	@echo "make security   the security suite"
	@echo "make live-gate  the live gate, including the incident re-enactment"
	@echo "make protected  verify the 12 protected files against PROTECTED.sha256"
	@echo "make manifests  kustomize build"
	@echo "make container  docker build + the container suite"
	@echo "make audit      pip-audit"
	@echo "make gates      everything that runs on a pull request"
	@echo
	@echo "Live tests are NOT a target here. They need a key and a separate"
	@echo "authorisation, and running them is a deliberate manual act."
	@echo "See docs/live-verification.md."

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest -m "$(OFFLINE_MARKERS)"

security:
	$(PYTHON) -m pytest -m "$(OFFLINE_MARKERS)" tests/security

live-gate:
	$(PYTHON) scripts/ci_assert_no_live.py
	$(PYTHON) scripts/ci_assert_incident_refused.py
	$(PYTHON) -m pytest -m "$(OFFLINE_MARKERS)" tests/security/test_live_gate.py

# Cross-platform on purpose. CI runs `sha256sum -c PROTECTED.sha256`, which is
# stronger -- raw bytes, no interpreter. This wrapper exists because a Windows
# working tree can hold CRLF where Git holds LF, and a gate that fails on a
# clean tree is a gate people learn to ignore.
protected:
	$(PYTHON) scripts/check_protected.py

manifests:
	kubectl kustomize k8s/ > /dev/null && echo "manifests render"

container:
	$(PYTHON) -m pytest -m "docker and not live"

audit:
	$(PYTHON) -m pip_audit --progress-spinner off

# The pull-request set, in the order that fails fastest.
gates: lint typecheck protected manifests security live-gate test
