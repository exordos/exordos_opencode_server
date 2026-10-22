# AGENTS.md

Context for any coding agent (Claude Code, OpenCode, Cursor, etc.) working in
this repository.

## What this is

`exordos_opencode_server` serves disposable OpenCode build workers from a hot
pool. A client orders a worker (a *lease*) with its SSH public key, gets a VM
with OpenCode, the Exordos CLI and an image build toolchain, and the worker is
destroyed when the lease is released.

The control plane is a MetaPaaS plugin built on `exordos_metapaas`. It has no
node or processes of its own: metapaas installs the wheel, applies the
migrations, mounts the API, and runs the plugin services.

**Read `README.md` before any non-trivial change.** It describes the models,
services, lease lifecycle, and API. This file covers only conventions and
commands.

Sibling repos on disk (`../exordos_metapaas`, `../exordos_core`,
`../exordos_s3`) are the source of truth for framework contracts. When in
doubt, read their code and docs instead of guessing.

## Naming

- Repository and Python package: `exordos_opencode_server`.
- Exordos element name: `opencode_server`.
- MetaPaaS slug: `opencode`.

Never mix them up: the element name appears in EM kinds
(`$opencode_server.types.opencode.*`), the slug in API paths and table names
(`/v1/types/opencode/`, `opencode_*`). Both are defined in
`exordos_opencode_server/constants.py`.

## Repo layout

```text
exordos_opencode_server/
├── exordos/
│   ├── exordos.yaml                  # element build config
│   ├── images/build_wheel.sh         # builds the plugin wheel artifact
│   ├── images/worker_install.sh      # worker image provisioning
│   └── manifests/
│       └── opencode_server.yaml.j2   # the plugin element
├── etc/opencode.jsonc                # system managed OpenCode config
├── exordos_opencode_server/
│   ├── definition.py                 # PaaSDefinition, the plugin entry point
│   ├── constants.py                  # slug, element name, statuses
│   ├── controlplane/
│   │   ├── dm/models.py              # Version, Pool, Worker, Lease
│   │   ├── api/                      # routes and controllers
│   │   ├── scheduler/service.py      # pool scheduler
│   │   └── infra/                    # worker builder, infra agent + scheduler
│   ├── migrations/
│   └── tests/unit/
├── tests/test_element.py             # manifest and build config checks
└── .github/workflows/                # tests and element publishing
```

## Commands

Python project managed with `uv` and `tox`.

```bash
make check     # unit tests, ruff, and shell syntax checks
make test      # tox -e py
make lint      # tox -e ruff
make format    # ruff --fix and ruff format
make wheel     # build the plugin wheel
make build     # exordos build (needs the exordos CLI, packer, KVM)
```

## Architecture rules

- The control plane is a MetaPaaS plugin. Do not add control-plane services,
  units, or a node of its own, and do not require changes to
  `exordos_metapaas`.
- A worker serves at most one lease and is destroyed afterwards. Never hand a
  worker that has ever had a client key to another lease.
- Clients reach workers over SSH only. Their keys are delivered as Exordos
  Core SSH keys (`/v1/secret/ssh_keys/`), never through config files. Do not
  start an OpenCode HTTP server on workers.
- Workers are written only by the control-plane services (scheduler and
  builder); the API writes pools and creates leases. Every column has one
  writer: the API changes a lease only through its own `release_requested`
  column (`Lease.request_release`), and `Lease.update` never writes it, so
  no row lock is needed. Never let the API write `status`.
- Pools and leases are project resources: the API scopes every call by the
  caller's project, and a lease belongs to its pool's project. There are no
  shared pools; every consumer declares its own.
- Pool configuration (flavor, hot pool size) lives in the API or in a
  manifest (`$opencode_server.types.opencode.pools`). The core-agent has no
  filter for it (`get_agent_filters` is empty), so it keeps to the pools it
  created from manifests and never touches API pools. `purge_before` is
  written only by `Pool.request_purge`, never by a whole-row update. The
  scheduler replaces free workers whose flavor snapshot differs from the
  pool (`Worker.has_flavor_of`); bound workers keep theirs.
- A pool stores the worker `image` itself, never a reference to a
  `Version` row. Manifest pools link `$...versions.$default:image`, so a
  new release of the element re-renders them through EM.

## Code conventions

- IAM permission names in the manifest must match the controllers exactly:
  `exordos_opencode_server.<policy>.<action>`, where `<policy>` is the
  controller's `__policy_name__`. A mismatch is a silent 403 at runtime;
  `exordos_opencode_server/tests/unit/test_definition.py`
  guards it.
- Never bind a permission to the owner role: Core grants a role's
  permissions in every project it is held in, so it would reach every
  project owner. Bind to the element's roles (`$core.iam.roles`).
- Use the well-known fixed UUIDs already present in the manifest (metapaas
  project, metapaas user). Do not invent new ones.
- Manifest grammar is specified in `../exordos_core/docs/em/manifest.md`.
- Every `.py` and shell file starts with the Apache-2.0 / Genesis Corporation
  license header. Copy it from an existing file.
- Verify manifest, API, and service contracts against current Exordos Core,
  MetaPaaS, and OpenCode documentation before changing them.

## Worker image

- Do not vendor OpenCode, Exordos CLI, or Packer sources or binaries. Download
  one pinned upstream release during the image build and verify its SHA-256
  checksum.
- Keep OpenCode automatic updates and sharing disabled through the system
  managed config (`/etc/opencode/opencode.jsonc`) so the runtime remains
  identical to the reviewed build artifact.

## Secrets and privacy

- Do not commit credentials, tokens, private URLs, infrastructure addresses,
  Workspace identifiers, user identifiers, or message contents.
- A pool's `opencode_config` may carry provider credentials: never log or
  print it.

## Testing and publishing

- Run `make check` before considering a change done or publishing it.
- Model, scheduler, and builder changes need unit tests in
  `exordos_opencode_server/tests/unit/`; manifest and build config changes
  need checks in `tests/test_element.py`.
- Build and deploy the element only through the `exordos` CLI.
- Write repository content, source comments, commit messages, and GitHub text
  in English.
- Do not commit `__pycache__/`, `.tox/`, `build/`, `dist/`, or `output/`.
