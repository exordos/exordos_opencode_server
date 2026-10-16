#    Copyright 2026 Genesis Corporation.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Control plane models.

The pair Lease/Worker mirrors Node/Machine in Exordos Core: a lease is what
a client orders, a worker is the VM that serves it. Workers are created
ahead of demand (the hot pool) and handed out to leases by the scheduler.
A worker is never reused: once a client had access to it, it is destroyed.
"""

import datetime

from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.common import exceptions as ra_e
from restalchemy.dm import models, properties, relationships, types
from restalchemy.storage.sql import orm

from exordos_opencode_server import constants as c

SSH_KEY_TYPES = frozenset(
    (
        "ssh-ed25519",
        "ssh-rsa",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "sk-ssh-ed25519@openssh.com",
        "sk-ecdsa-sha2-nistp256@openssh.com",
    )
)


class SSHPublicKey(types.String):
    """A single OpenSSH public key line: `<type> <base64> [comment]`."""

    def __init__(self, **kwargs):
        kwargs.setdefault("min_length", 16)
        kwargs.setdefault("max_length", 16384)
        super().__init__(**kwargs)

    def validate(self, value):
        if not super().validate(value):
            return False
        if "\n" in value or "\r" in value:
            return False
        parts = value.split()
        return len(parts) >= 2 and parts[0] in SSH_KEY_TYPES


class ImmutableFieldError(ra_e.ValidationErrorException):
    message = "Field %(field)s can't be changed."


class Version(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    ua_models.TargetResourceMixin,
):
    """A worker image, declared by the element manifest.

    The catalog is synced from Exordos Core by the metapaas core-agent, so
    it is read-only through the API. The manifest keeps one stable entry
    that every release of the element updates in place. Pools copy the
    image (not reference the entry), so a new image changes the pool
    itself, both in the manifest of the pool and in the database.
    """

    __tablename__ = "opencode_versions"

    image = properties.property(
        types.String(min_length=1, max_length=255),
        required=True,
    )


class Pool(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    ua_models.TargetResourceMixin,
):
    """The hot pool configuration, managed by an administrator.

    A pool is created either through the API or by an element manifest
    (`$opencode_server.types.opencode.pools`). The metapaas core-agent syncs
    only the pools whose `description` carries the EM project id, so the
    pools created through the API are never touched by it.

    Changing the flavor (image, cores, ram, disk_size,
    ssh_user, opencode_config) replaces the free workers: the scheduler
    drops every free worker whose snapshot differs from the pool. Workers
    bound to a lease keep the flavor they were created with. The
    `purge_idle` action drops the free workers regardless of the flavor.
    """

    __tablename__ = "opencode_pools"

    name = properties.property(
        types.String(min_length=1, max_length=255),
        required=True,
    )
    # A worker image, usually taken from the version catalog.
    image = properties.property(
        types.String(min_length=1, max_length=255),
        required=True,
    )
    cores = properties.property(
        types.Integer(min_value=1, max_value=128),
        default=4,
    )
    ram = properties.property(
        types.Integer(min_value=1024, max_value=1024**2),
        default=8192,
    )
    disk_size = properties.property(
        types.Integer(min_value=10, max_value=4096),
        default=40,
    )
    # How many free, ready workers are kept. Zero disables the hot pool:
    # every lease then waits for a worker created on demand.
    hot_size = properties.property(
        types.Integer(min_value=0, max_value=1024),
        default=1,
    )
    # The upper bound for all workers of the pool, busy ones included.
    max_workers = properties.property(
        types.Integer(min_value=1, max_value=1024),
        default=10,
    )
    ssh_user = properties.property(
        types.String(min_length=1, max_length=64),
        default=c.DEFAULT_SSH_USER,
    )
    # The content of OpenCode's opencode.json on every worker: providers,
    # models and so on. Delivered before the worker is handed out.
    opencode_config = properties.property(types.Dict(), default=dict)
    # Set by the `purge_idle` action: free workers created before this
    # moment are dropped by the scheduler. Written only by the API (see
    # `request_purge`): the rest of the row is also written by the
    # core-agent for pools declared in a manifest.
    purge_before = properties.property(
        types.AllowNone(types.UTCDateTimeZ()),
        default=None,
    )

    def get_data_properties(self):
        # `update()` writes whole rows, which would overwrite a purge request
        # that arrived after the row was read. Leave the column out of it.
        result = super().get_data_properties()
        result.pop("purge_before", None)
        return result

    def request_purge(self, session=None):
        """Drop the free workers created so far, touching nothing else."""
        self.purge_before = datetime.datetime.now(datetime.UTC)
        self.properties["updated_at"].set_value_force(self.purge_before)
        with self._get_engine().session_manager(session=session) as s:
            self.get_table().update(
                engine=self._get_engine(),
                ids=self._get_prepared_data(self.get_id_properties()),
                data=self._get_prepared_data(
                    {
                        name: self.properties[name]
                        for name in ("purge_before", "updated_at")
                    }
                ),
                session=s,
            )


class Lease(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithProject,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    """A client's order for a worker.

    The client gives its public key and gets the worker's address and login
    once the key is on the worker. Releasing the lease destroys the worker.
    """

    __tablename__ = "opencode_leases"

    pool = relationships.relationship(Pool, required=True)
    ssh_public_key = properties.property(SSHPublicKey(), required=True)
    # Optional safety net: an expired lease is released automatically.
    expires_at = properties.property(
        types.AllowNone(types.UTCDateTimeZ()),
        default=None,
    )
    status = properties.property(
        types.Enum([s.value for s in c.LeaseStatus]),
        default=c.LeaseStatus.NEW.value,
    )
    worker = properties.property(types.AllowNone(types.UUID()), default=None)
    ipv4 = properties.property(
        types.AllowNone(types.String(max_length=15)),
        default=None,
    )
    ssh_user = properties.property(
        types.AllowNone(types.String(max_length=64)),
        default=None,
    )
    error = properties.property(types.String(max_length=1024), default="")
    # How many workers already failed this lease.
    attempts = properties.property(types.Integer(min_value=0), default=0)
    # The client asked to release the lease. Written only by the API (see
    # `request_release`); `status` is written only by the scheduler. Each
    # side owns its own columns, so neither needs a row lock.
    release_requested = properties.property(types.Boolean(), default=False)

    def get_data_properties(self):
        # `update()` writes whole rows, which would overwrite a release
        # request that arrived after the scheduler read the lease. Leave
        # the API-owned column out of it.
        result = super().get_data_properties()
        result.pop("release_requested", None)
        return result

    def request_release(self, session=None):
        """Ask the scheduler to release the lease, touching nothing else."""
        if self.release_requested:
            return
        self.release_requested = True
        self.properties["updated_at"].set_value_force(
            datetime.datetime.now(datetime.UTC)
        )
        with self._get_engine().session_manager(session=session) as s:
            self.get_table().update(
                engine=self._get_engine(),
                ids=self._get_prepared_data(self.get_id_properties()),
                data=self._get_prepared_data(
                    {
                        name: self.properties[name]
                        for name in ("release_requested", "updated_at")
                    }
                ),
                session=s,
            )

    def update(self, session=None, force=False):
        for field in ("pool", "ssh_public_key"):
            if self.properties[field].is_dirty():
                raise ImmutableFieldError(field=field)
        super().update(session=session, force=force)

    def is_expired(self, now: datetime.datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        now = now or datetime.datetime.now(datetime.UTC)
        return self.expires_at <= now

    def is_final(self) -> bool:
        return self.status in c.FINAL_LEASE_STATUSES


class Worker(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    """A disposable VM serving at most one lease.

    The uuid is the uuid of the worker's node in Exordos Core. The flavor is
    a snapshot of the pool's flavor at creation time.
    """

    __tablename__ = "opencode_workers"

    name = properties.property(
        types.String(min_length=1, max_length=255),
        required=True,
    )
    pool = relationships.relationship(Pool, required=True)
    status = properties.property(
        types.Enum([s.value for s in c.WorkerStatus]),
        default=c.WorkerStatus.NEW.value,
    )
    cores = properties.property(
        types.Integer(min_value=1, max_value=128),
        required=True,
    )
    ram = properties.property(
        types.Integer(min_value=1024, max_value=1024**2),
        required=True,
    )
    disk_size = properties.property(
        types.Integer(min_value=10, max_value=4096),
        required=True,
    )
    image = properties.property(
        types.String(min_length=1, max_length=255),
        required=True,
    )
    ssh_user = properties.property(
        types.String(min_length=1, max_length=64),
        default=c.DEFAULT_SSH_USER,
    )
    opencode_config = properties.property(types.Dict(), default=dict)
    # Set together when the worker is bound. A worker that ever had a key
    # is never handed out again, even if its lease is gone.
    lease = properties.property(types.AllowNone(types.UUID()), default=None)
    ssh_public_key = properties.property(
        types.AllowNone(SSHPublicKey()),
        default=None,
    )
    ipv4 = properties.property(
        types.AllowNone(types.String(max_length=15)),
        default=None,
    )
    ready_at = properties.property(
        types.AllowNone(types.UTCDateTimeZ()),
        default=None,
    )

    @classmethod
    def from_pool(cls, pool: Pool, **kwargs) -> "Worker":
        worker = cls(
            name="opencode-worker",
            pool=pool,
            cores=pool.cores,
            ram=pool.ram,
            disk_size=pool.disk_size,
            image=pool.image,
            ssh_user=pool.ssh_user,
            opencode_config=pool.opencode_config,
            **kwargs,
        )
        worker.name = f"opencode-worker-{str(worker.uuid)[:8]}"
        return worker

    def has_flavor_of(self, pool: Pool) -> bool:
        """The worker was created with the pool's current flavor."""
        return all(
            getattr(self, name) == getattr(pool, name)
            for name in (
                "image",
                "cores",
                "ram",
                "disk_size",
                "ssh_user",
                "opencode_config",
            )
        )

    def is_free(self) -> bool:
        """The worker can still be handed out to a lease."""
        return (
            self.lease is None
            and self.ssh_public_key is None
            and self.status in c.FREE_WORKER_STATUSES
        )

    def bind(self, lease: Lease) -> None:
        self.lease = lease.uuid
        self.ssh_public_key = lease.ssh_public_key
        # A worker still being provisioned keeps its status; the builder
        # moves it on once the node is up.
        if self.status == c.WorkerStatus.READY.value:
            self.status = c.WorkerStatus.RESERVED.value
