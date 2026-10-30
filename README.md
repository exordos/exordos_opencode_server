# Exordos OpenCode Server

`opencode_server` serves disposable OpenCode build workers on demand. A client
orders a worker (a *lease*) with its SSH public key and gets a VM with the
OpenCode CLI, the Exordos CLI and an image build toolchain (Packer, QEMU/KVM)
within seconds, because workers are kept ready in a hot pool. When the client
releases the lease, the worker is destroyed; workers are never reused.

The main client is [Exordos Elementizer](https://github.com/exordos/exordos_elementizer),
which orders one worker per application build.

## Architecture

The control plane is a [MetaPaaS](https://github.com/exordos/exordos_metapaas)
plugin: the element registers the `opencode` type, and metapaas installs the
plugin, applies its migrations, mounts its API, and runs its services. It has
no node or processes of its own.

| Model | What it is |
|---|---|
| `Version` | A worker image, declared by the element manifest (read-only). Pools copy its `image`. |
| `Pool` | The hot pool configuration of a project: flavor and sizes. Declared by a manifest or managed via the API. |
| `Worker` | One disposable VM (a Core node). Mirrors a Core `Machine`. |
| `Lease` | A client's order for a worker. Mirrors a Core `Node`. |

Services, all inside the metapaas gservice:

- **Pool scheduler**: binds pending leases to ready workers (or creates a
  worker on demand), keeps `hot_size` free workers, releases expired leases,
  destroys workers that are no longer needed.
- **Worker builder**: declares the Core resources of every worker (node,
  OpenCode config, the lease's SSH key) and derives the worker status from
  what Core reports.
- **Infra agent and its scheduler**: materialize the worker's Core nodes, configs and
  SSH keys under plugin-prefixed resource kinds (`opencode_node`,
  `opencode_config`, `opencode_ssh_key`), so no other agent competes for them.

A lease's lifecycle:

1. `NEW`: waiting for a worker.
2. `SCHEDULED`: a worker is bound; Core is delivering the client's key.
3. `ACTIVE`: the key is on the worker; `ipv4` and `ssh_user` are set.
4. `RELEASED`: the worker is destroyed. A release (or `DELETE`) only sets
   `release_requested`; the scheduler destroys the worker on its next
   iteration, so `status` changes a few seconds later.

A worker counts as failed only after it has stayed in `ERROR` for two
minutes (`WORKER_FAILURE_GRACE`): Core may report a node that is up as not
`ACTIVE` for a moment, and such a flap changes nothing. Once the node has
been up, the worker keeps its OpenCode config and the lease's key whatever
Core reports about the node.

A lease whose worker failed before the lease became `ACTIVE` is requeued
to another worker; it fails with `ERROR` after its workers failed to start
three times. A lease that is already `ACTIVE` never goes back to
`SCHEDULED` and is never moved: a new worker
would have another address and none of the client's work, so the lease
fails with `ERROR` right away and the client decides what to do next.

## API

Mounted by metapaas under `/api/metapaas/v1/types/opencode/`:

| Path | Methods |
|---|---|
| `versions/` | list, get |
| `pools/` | create, list, get, update, delete |
| `pools/<uuid>/actions/purge_idle/invoke` | POST: drop all free workers |
| `pools/<uuid>/actions/stats` | GET: workers and leases by status |
| `pools/<uuid>/workers/` | list, get |
| `pools/<uuid>/leases/` | create, list, get, delete |
| `pools/<uuid>/leases/<uuid>/actions/release/invoke` | POST: release the worker |

Pools and their leases belong to a project: every call is scoped by the
project of the caller's token, so a project sees and uses only its own
pools. Access goes through the element's own roles, never the owner role:
Core grants a role's permissions wherever the role is held, so a permission
bound to the owner role would reach every project owner.

| Role | Grants | Export |
|---|---|---|
| `opencode_pool_admin` | everything above | `pool_admin_role` |
| `opencode_lease_client` | `versions/` and `pools/` read, leases | `lease_client_role` |
| `opencode_infra` | Core's `secret.ssh_key.*`; held by the metapaas user (the infra agent) | |

A consumer binds its user to an imported role in its own project and asks
for a token scoped to that project. A binding without a project grants the
role in every project, for an operator with a token without a project.

### Setting up a pool

A consumer usually declares its pool in its own manifest, in its own
project, and orders leases from it:

```yaml
requirements:
  opencode_server:
    from_version: "<version>"

imports:
  opencode_version:
    element: "$opencode_server"
    kind: "resource"
    link: "$opencode_server.types.opencode.versions.$default"
  opencode_lease_client:
    element: "$opencode_server"
    kind: "resource"
    link: "$core.iam.roles.$opencode_lease_client"

resources:
  $core.iam.rolebinding:
    my_user_opencode:
      role: $<element>.imports.$opencode_lease_client:uuid
      user: $core.iam.users.$my_user:uuid
      project: $core.iam.projects.$my_project:uuid

  $opencode_server.types.opencode.pools:
    my_pool:
      name: "my_pool"
      project_id: $core.iam.projects.$my_project:uuid
      image: $<element>.imports.$opencode_version:image
      hot_size: 1
      opencode_config: {"model": "<provider>/<model>", "provider": {...}}
```

`image` is a copy of the version's image: a new release of opencode_server
updates it in place, and EM re-renders the pool, which replaces its free
workers. The core-agent syncs only the pools it created from a manifest.
Change a manifest pool through its manifest, not the API: the next
reconciliation reverts API edits to the fields the manifest sets.

A pool can also be created through the API, with a token of the project
it goes to:

```bash
curl -X POST .../api/metapaas/v1/types/opencode/pools/ -d '{
  "name": "default",
  "image": "<image of a version from versions/>",
  "cores": 4,
  "ram": 8192,
  "disk_size": 50,
  "hot_size": 2,
  "max_workers": 10,
  "opencode_config": {"model": "<provider>/<model>", "provider": {...}}
}'
```

`opencode_config` becomes `~/.config/opencode/opencode.json` on every worker.
The lease holder owns that file and has `sudo` on the worker, and so does
whatever runs there: a provider key in it is readable by them. Give a pool
a key of its own, limited by the provider, never a shared one.
A flavor change (`image`, `cores`, `ram`, `disk_size`,
`ssh_user`, `opencode_config`) replaces the free workers automatically;
workers already handed out to a lease keep their flavor. `purge_idle` drops
the free workers regardless of the flavor. `hot_size` may be `0`: every
lease then waits for a worker to boot.

### Using a worker

```bash
curl -X POST .../pools/<pool>/leases/ -d '{
  "name": "build-42",
  "ssh_public_key": "ssh-ed25519 AAAA... builder",
  "expires_at": "2026-01-01T12:00:00Z"
}'
# poll until "status": "ACTIVE", then
ssh <ssh_user>@<ipv4>
# when done
curl -X POST .../pools/<pool>/leases/<lease>/actions/release/invoke
```

`expires_at` is optional; an expired lease is released automatically.

## Worker image

The `opencode_worker` image contains pinned, checksum-verified releases of
OpenCode, the Exordos CLI, Packer and uv, plus QEMU. The `ubuntu` user has
passwordless `sudo` and KVM access; the VM is single-tenant and destroyed with
the lease. The `builder` user is for builds of code the client does not trust
(an `exordos build`, whose artifact scripts run on the host and whose images
take KVM): it has KVM and packer's plugins, but no `sudo` and no access to
`ubuntu`'s home, where the pool's `opencode.json` with the model provider's
key is. `ubuntu` runs it with `sudo -u builder`. SSH accepts keys only. OpenCode automatic updates and sharing are
disabled by the system managed config `/etc/opencode/opencode.jsonc`, which
takes precedence over the pool's configuration. `/tmp` is on the disk, not
the tmpfs systemd mounts by default: image builds keep their disks there.

Image builds on a worker need nested virtualization: the hypervisors must
expose VMX/SVM to the worker VMs.

## Development

```bash
uv sync --all-extras
make check
```

`make check` runs the unit and element tests, Ruff, and Bash syntax checks.

## Build

```bash
make build
```

The element is built with the Exordos CLI and produces the plugin wheel and
the worker image. See the official Exordos documentation for
[element builds](https://exordos.github.io/exordos_core/app-developer-guide/build.html)
and [manifests](https://exordos.github.io/exordos_core/em/manifest.html).

## Release

The `Exordos element` GitHub Actions workflow builds the element on branch
pushes, external pull requests, release tags, and manual runs. Every branch
push publishes its immutable dev version to the configured Exordos element
repository without changing `latest`. A pushed stable SemVer tag
(`MAJOR.MINOR.PATCH`) publishes the stable version and updates `latest`. Both
publication paths require `tests.yaml` to pass for the exact same commit.

Publication uses the repository or organization Actions secret `PUSH_CFG`: the
base64-encoded contents of an `exordos.push.yaml` file. Published versions are
immutable: the workflow does not use `exordos push --force`.
