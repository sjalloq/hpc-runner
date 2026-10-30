# Scheduler testbeds (TESTBED_PLAN.md). Every target takes FLAVOUR=<name>
# (default soge) and the Python version PY=<ver> (default 3.12) where relevant.
#
#   make -C testbed sge-up          build the image, start the stack, wait for ready.sh
#   make -C testbed sge-test        run tests/integration inside login, copy the JUnit XML out
#   make -C testbed sge-shell       shell in login as tester with the venv on PATH
#   make -C testbed sge-logs        container logs (SERVICE=master to narrow)
#   make -C testbed sge-clean       clean.sh: delete all jobs, wait for an empty queue
#   make -C testbed sge-down        stop and remove containers, network and volumes

FLAVOUR ?= soge
PY ?= 3.12
SERVICE ?=
READY_TIMEOUT ?= 90
# SLOW_ACCOUNTING=1 starts the master with a 15 s accounting flush (D-T6).
SLOW_ACCOUNTING ?=
export ACCOUNTING_FLUSH ?= $(if $(SLOW_ACCOUNTING),00:00:15,00:00:01)
# Extra pytest arguments for sge-test, e.g. PYTEST_ARGS="-k submit -x".
PYTEST_ARGS ?=

COMPOSE := docker compose -f sge/$(FLAVOUR)/compose.yml -p hpc-testbed-$(FLAVOUR)
VENV_BIN := /opt/venv/$(PY)/bin

.PHONY: sge-up sge-down sge-shell sge-logs sge-test sge-clean

sge-up:
	$(COMPOSE) build
	$(COMPOSE) up -d --wait
	$(COMPOSE) exec -T login /opt/testbed/common/wait-for.sh $(READY_TIMEOUT) 3 -- /opt/testbed/ready.sh

sge-down:
	$(COMPOSE) down -v --remove-orphans

sge-shell:
	$(COMPOSE) exec -u tester -w /src -e PATH=$(VENV_BIN):/usr/local/bin:/usr/bin:/bin login bash

sge-logs:
	$(COMPOSE) logs --no-color $(SERVICE)

# Runs inside login as tester (the submitting shell is the test process,
# plan section 4.1). `-m integration` overrides pyproject's `-m 'not
# integration'` addopts. The JUnit XML is copied next to this Makefile.
sge-test:
	$(COMPOSE) exec -T -u tester -w /src login $(VENV_BIN)/pytest tests/integration -m integration \
		--cluster sge --flavour $(FLAVOUR) --junitxml=/work/junit-$(PY).xml $(PYTEST_ARGS); \
	rc=$$?; \
	$(COMPOSE) cp login:/work/junit-$(PY).xml junit-$(PY).xml; \
	exit $$rc

sge-clean:
	$(COMPOSE) exec -T login /opt/testbed/clean.sh
