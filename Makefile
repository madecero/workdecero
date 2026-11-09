.PHONY: dev-setup test leak-check validate authors check

# One-time wiring after cloning: turns on the leak-guard git hooks.
dev-setup:
	git config core.hooksPath hooks
	@echo "Hooks on. Private denylist: $${WORKDECERO_DENYLIST:-~/.config/workdecero-dev/denylist.txt}"

test:
	python3 -m unittest discover -s tests -v

leak-check:
	python3 dev/leak_guard.py --tree

validate:
	python3 dev/validate_manifests.py .

authors:
	python3 dev/check_authors.py HEAD

check: leak-check validate authors test
