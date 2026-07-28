SHELL := /bin/bash

PYTHON ?= python3.11
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python
SETUP_STAMP := $(VENV)/.usage-monitor-setup.stamp
USAGECTL := $(VENV_PYTHON) scripts/usagectl.py

HOST ?= 127.0.0.1
PORT ?= 9097
REFRESH_INTERVAL ?=
MENUBAR_INTERVAL ?=
DASHBOARD_URL ?= http://$(HOST):$(PORT)
APP_HOME ?= $(HOME)/.config/usagemon
LOG_DIR ?= $(APP_HOME)/logs
OUT_DIR ?= .launchagents
LAUNCHAGENTS_DIR ?= $(HOME)/Library/LaunchAgents

DEFAULT_LABEL_PREFIX ?= com.usage-monitor
LEGACY_LABEL_PREFIXES ?= com.hermes-usage-monitor
KNOWN_LABEL_PREFIXES := $(DEFAULT_LABEL_PREFIX) $(LEGACY_LABEL_PREFIXES)
DETECTED_LABEL_PREFIX := $(shell for prefix in $(KNOWN_LABEL_PREFIXES); do \
	if [ -e "$(LAUNCHAGENTS_DIR)/$$prefix.server.plist" ] || [ -e "$(LAUNCHAGENTS_DIR)/$$prefix.menubar.plist" ]; then \
		echo "$$prefix"; exit 0; \
	fi; \
done; echo "$(DEFAULT_LABEL_PREFIX)")
LABEL_PREFIX ?= $(DETECTED_LABEL_PREFIX)
SERVER_PLIST := $(LAUNCHAGENTS_DIR)/$(LABEL_PREFIX).server.plist
MENUBAR_PLIST := $(LAUNCHAGENTS_DIR)/$(LABEL_PREFIX).menubar.plist
REFRESH_ARG := $(if $(REFRESH_INTERVAL),--refresh-interval "$(REFRESH_INTERVAL)",)
MENUBAR_ARG := $(if $(MENUBAR_INTERVAL),--menubar-interval "$(MENUBAR_INTERVAL)",)
TRAY_INTERVAL_ARG := $(if $(MENUBAR_INTERVAL),--interval "$(MENUBAR_INTERVAL)",)

# Standalone by default. Override AGENT_PYTHON only when you intentionally want
# LaunchAgents to run under another interpreter.
AGENT_PYTHON ?= $(CURDIR)/$(VENV_PYTHON)

.PHONY: help setup install install-dev install-hermes-integration test lint security-scan security-scan-history scan-secrets status server tray autostart-generate install-tray uninstall-tray restart-tray update status-tray logs clean clean-launchagents

help:
	@echo "Usage Monitor targets:"
	@echo "  make setup                 create/update .venv with dev+menubar extras"
	@echo "  make test                  pytest + compileall + shell syntax + diff check"
	@echo "  make security-scan         run gitleaks + trufflehog on current files"
	@echo "  make security-scan-history scan git history with both tools"
	@echo "  make status                print current provider status"
	@echo "  make server                run local dashboard/API foreground at $(DASHBOARD_URL)"
	@echo "  make tray                  run macOS menu bar app foreground"
	@echo "  make install / install-tray install/update standalone LaunchAgents"
	@echo "  make uninstall-tray        unload/remove standalone LaunchAgents"
	@echo "  make restart-tray          reinstall/reload standalone LaunchAgents"
	@echo "  make status-tray           print launchctl state"
	@echo "  make logs                  tail logs in LOG_DIR=$(LOG_DIR)"
	@echo "  make install-hermes-integration  optional legacy Hermes skill/plugin install"
	@echo ""
	@echo "Config defaults: APP_HOME=$(APP_HOME) PORT=$(PORT) intervals from config.yaml unless REFRESH_INTERVAL/MENUBAR_INTERVAL are set"

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV)

$(SETUP_STAMP): pyproject.toml $(VENV_PYTHON)
	$(VENV_PYTHON) -m ensurepip --upgrade
	$(VENV_PYTHON) -m pip install -U pip
	$(VENV_PYTHON) -m pip install -e '.[dev,menubar]'
	touch $(SETUP_STAMP)

setup: $(SETUP_STAMP)

install: install-tray

install-dev: setup

status: setup
	$(USAGECTL) status

test: setup
	$(VENV_PYTHON) -m pytest -q
	$(VENV_PYTHON) -m compileall -q usage_monitor_app tests scripts plugin
	bash -n install.sh uninstall.sh
	git diff --check

lint: test

security-scan scan-secrets:
	gitleaks dir . --no-banner
	trufflehog filesystem . --exclude-paths .trufflehog-exclude --results=verified --fail --no-update

security-scan-history:
	gitleaks git . --no-banner
	trufflehog git file://$(CURDIR) --exclude-paths .trufflehog-exclude --results=verified --fail --no-update

server: setup
	$(USAGECTL) serve --host $(HOST) --port $(PORT) $(REFRESH_ARG)

tray: setup
	$(USAGECTL) menubar $(TRAY_INTERVAL_ARG) --dashboard-url $(DASHBOARD_URL)

autostart-generate: setup
	mkdir -p "$(OUT_DIR)" "$(LOG_DIR)"
	$(USAGECTL) autostart \
		--output-dir "$(OUT_DIR)" \
		--python "$(AGENT_PYTHON)" \
		--working-dir "$(CURDIR)" \
		--usagectl "$(CURDIR)/scripts/usagectl.py" \
		--host "$(HOST)" \
		--port "$(PORT)" \
		--log-dir "$(LOG_DIR)" \
		--dashboard-url "$(DASHBOARD_URL)" \
		$(REFRESH_ARG) \
		$(MENUBAR_ARG) \
		--label-prefix "$(LABEL_PREFIX)"

install-tray: autostart-generate
	mkdir -p "$(LAUNCHAGENTS_DIR)" "$(LOG_DIR)"
	@echo "Using label prefix: $(LABEL_PREFIX)"
	@for prefix in $(KNOWN_LABEL_PREFIXES); do \
		launchctl bootout "gui/$$(id -u)" "$(LAUNCHAGENTS_DIR)/$$prefix.server.plist" 2>/dev/null || true; \
		launchctl bootout "gui/$$(id -u)" "$(LAUNCHAGENTS_DIR)/$$prefix.menubar.plist" 2>/dev/null || true; \
	done
	cp "$(OUT_DIR)/$(LABEL_PREFIX).server.plist" "$(SERVER_PLIST)"
	cp "$(OUT_DIR)/$(LABEL_PREFIX).menubar.plist" "$(MENUBAR_PLIST)"
	@for prefix in $(KNOWN_LABEL_PREFIXES); do \
		if [ "$$prefix" != "$(LABEL_PREFIX)" ]; then \
			rm -f "$(LAUNCHAGENTS_DIR)/$$prefix.server.plist" "$(LAUNCHAGENTS_DIR)/$$prefix.menubar.plist"; \
		fi; \
	done
	launchctl bootstrap "gui/$$(id -u)" "$(SERVER_PLIST)"
	launchctl bootstrap "gui/$$(id -u)" "$(MENUBAR_PLIST)"
	@echo "Tray installed. Dashboard: $(DASHBOARD_URL)"

uninstall-tray:
	@for prefix in $(KNOWN_LABEL_PREFIXES); do \
		launchctl bootout "gui/$$(id -u)" "$(LAUNCHAGENTS_DIR)/$$prefix.server.plist" 2>/dev/null || true; \
		launchctl bootout "gui/$$(id -u)" "$(LAUNCHAGENTS_DIR)/$$prefix.menubar.plist" 2>/dev/null || true; \
		rm -f "$(LAUNCHAGENTS_DIR)/$$prefix.server.plist" "$(LAUNCHAGENTS_DIR)/$$prefix.menubar.plist"; \
	done
	@echo "Tray removed (logs preserved in $(LOG_DIR))."

restart-tray: uninstall-tray install-tray

update: setup restart-tray
	@echo "Update complete. Dashboard: $(DASHBOARD_URL)"

install-hermes-integration: setup
	$(USAGECTL) install

status-tray:
	launchctl print "gui/$$(id -u)/$(LABEL_PREFIX).server" || true
	launchctl print "gui/$$(id -u)/$(LABEL_PREFIX).menubar" || true

logs:
	mkdir -p "$(LOG_DIR)"
	tail -n 80 -f "$(LOG_DIR)"/*.log

clean-launchagents:
	rm -rf "$(OUT_DIR)"

clean: clean-launchagents
	rm -rf .pytest_cache usage_monitor.egg-info hermes_usage_monitor.egg-info
