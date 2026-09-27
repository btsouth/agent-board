# agent-board development tasks.
#
# `make check` is the gate: every suite offline, no game client, no network. If it
# passes, the addon loads, the two halves agree on the wire format, the release lint
# is clean and the release zip is the shape the client wants. Also what CI runs.
# (No count here on purpose: it has been wrong twice.)

SHELL := /bin/bash

# The bridge talks to the desktop app over a websocket, which needs the Agent
# venv's packages; the offline suites do not, so they fall back to system python.
HERMES_VENV ?= $(HOME)/.hermes/hermes-agent/venv/bin/python
PY := $(shell test -x "$(HERMES_VENV)" && echo $(HERMES_VENV) || echo python3)
LUA ?= lua5.1
LUAC ?= luac5.1
ADDON ?= /mnt/data/Games/World of Warcraft/_classic_beta_/Interface/AddOns

.DEFAULT_GOAL := help

.PHONY: help check check-lua check-node check-python install publish package icon banner interface preview lint clean

help: ## Show the targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

check: check-lua check-node check-python ## Run every gate (what CI runs)

check-lua: ## The addon under a stub client API
	$(LUA) tests/wow_stub.lua

check-node: ## Omarchy theme discovery and JavaScript syntax
	node tests/omarchy_theme_test.js
	node tests/message_queue_test.js
	node tests/badge_state_test.js
	node tests/markdown_test.js
	node tests/visibility_test.js
	node tests/provider_runtime_test.cjs

check-python: ## Roster, wire format, hostile input, hosts, lint, package, notifications
	$(PY) tests/roster_test.py
	$(PY) tests/roundtrip.py
	$(PY) tests/hostile_test.py
	$(PY) tests/hosts_test.py
	$(PY) tests/addon_checks.py
	$(PY) tests/package_test.py
	$(PY) tests/preview_test.py
	$(PY) tests/notify_test.py
	$(PY) tests/live_test.py -v
	$(PY) tests/overlay_regression_test.py -v
	$(PY) tests/t3_test.py -v
	$(PY) tests/runtime_test.py -v
	$(PY) tests/bootstrap_test.py
	$(PY) tests/setup_test.py -v
	$(PY) tests/update_test.py -v
	$(PY) tests/service_test.py
	$(PY) tests/demo_test.py

lint: ## Compile Lua and validate the bootstrap shell
	bash -n install.sh bin/agent-board
	@for file in addon/AgentBoard/*.lua; do $(LUAC) -p $$file || exit 1; done
	@if command -v node >/dev/null; then for file in overlay/*.js agentboard/providers/*.mjs; do node --check $$file || exit 1; done; fi
	@echo "shell, Lua, and JavaScript syntax checks passed"

install: ## Copy the addon into the client and publish a snapshot
	./bin/agent-board wow install --force
	./bin/agent-board wow publish

publish: ## Refresh the snapshot in the client without touching the code
	./bin/agent-board wow publish

package: ## Build the release zip into dist/ (refuses an unrecorded or unempty release)
	$(PY) scripts/package_addon.py

icon: ## Turn generated art into the addon icon (make icon MASTER=~/Downloads/emblem.png)
	@test -n "$(MASTER)" || { echo "usage: make icon MASTER=path/to/emblem.png  (512px or larger, transparent)"; exit 1; }
	$(PY) scripts/make_icon.py "$(MASTER)"

banner: ## Compose the store-page banner: emblem + a rendered panel + wordmark
	$(PY) scripts/make_banner.py

interface: ## Move the interface pin to a new client build (make interface IFACE=16002)
	@test -n "$(IFACE)" || { echo "usage: make interface IFACE=16002  (from /run print(GetBuildInfo()) in game)"; exit 1; }
	$(PY) scripts/bump_interface.py "$(IFACE)"

preview: ## Render the panel against the live roster into docs/panel-preview.html
	$(PY) scripts/panel_preview.py

clean: ## Remove caches and generated previews
	rm -rf __pycache__ agentboard/__pycache__ tests/__pycache__ .pytest_cache
