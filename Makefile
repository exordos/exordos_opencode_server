SHELL := bash

.PHONY: check test lint format build wheel

check:
	tox -e py,ruff
	bash -n \
		exordos/images/build_wheel.sh \
		exordos/images/worker_install.sh

test:
	tox -e py

lint:
	tox -e ruff

format:
	uv run --extra ruff ruff check --fix .
	uv run --extra ruff ruff format .

wheel:
	exordos/images/build_wheel.sh

build:
	exordos build . --exordos-cfg-file exordos/exordos.yaml
