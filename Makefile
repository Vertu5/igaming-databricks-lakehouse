PY := .venv/bin/python
PROFILE ?= igaming

.PHONY: check validate test-dev deploy-dev deploy-prod run-prod hooks

# Local CI gate: unit tests + bundle validation for both environments.
check:
	$(PY) -m unittest discover -s tests -v
	databricks bundle validate -t dev -p $(PROFILE)
	databricks bundle validate -t prod -p $(PROFILE)

# Fast feedback loop: deploy to dev and run the whole pipeline on the 5 percent sample.
test-dev:
	databricks bundle deploy -t dev -p $(PROFILE)
	databricks bundle run igaming_pipeline -t dev -p $(PROFILE)

# Continuous delivery: only after `make check` and a green `make test-dev`.
deploy-prod: check
	databricks bundle deploy -t prod -p $(PROFILE)

run-prod:
	databricks bundle run igaming_pipeline -t prod -p $(PROFILE)

# One-time setup: run `make check` automatically before every `git push`.
hooks:
	git config core.hooksPath .githooks
	chmod +x .githooks/pre-push
