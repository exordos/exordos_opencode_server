SHELL := bash

.PHONY: check build

check:
	tox -e py,ruff
	bash -n \
		exordos/images/install.sh \
		exordos/images/bootstrap.sh \
		scripts/opencode-server-health \
		scripts/opencode-server-reload \
		scripts/opencode-server-validate

build:
	exordos build . --exordos-cfg-file exordos/exordos.yaml
