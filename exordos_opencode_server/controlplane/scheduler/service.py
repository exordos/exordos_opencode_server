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

"""The pool scheduler: hands workers out to leases and keeps the pool hot.

Like the node scheduler of Exordos Core, which puts a Node on an idle
Machine or creates a new Machine for it, this scheduler binds a lease to a
ready worker or creates a worker for it. On top of that it keeps
`hot_size` free workers around so a lease rarely waits for a VM to boot.

One iteration per pool runs in one transaction. The leases of the pool
are written only by this scheduler, except for `release_requested`, the one
column the API owns (`Lease.update` never writes it). A release requested
during an iteration is picked up by the next one, so no row lock is needed.
Workers are written only by the control plane services (this scheduler
and the builder), which run one after another in the metapaas gservice.

`plan_pool` holds the whole decision and touches no storage; the service
loads the pool state, calls it and persists the result.
"""

import dataclasses
import datetime
import logging

from gcl_looper.services import basic
from restalchemy.common import contexts
from restalchemy.dm import filters as dm_filters

from exordos_opencode_server import constants as c
from exordos_opencode_server.controlplane.dm import models

LOG = logging.getLogger(__name__)


@dataclasses.dataclass
class PoolPlan:
    # Leases whose fields changed.
    leases: list[models.Lease] = dataclasses.field(default_factory=list)
    # Existing workers whose fields changed.
    workers: list[models.Worker] = dataclasses.field(default_factory=list)
    # Workers to create.
    new_workers: list[models.Worker] = dataclasses.field(default_factory=list)
    # Workers to destroy.
    obsolete_workers: list[models.Worker] = dataclasses.field(default_factory=list)


def _mark(items: list, item) -> None:
    if all(i is not item for i in items):
        items.append(item)


def _set(obj, **values) -> bool:
    changed = False
    for name, value in values.items():
        if getattr(obj, name) != value:
            setattr(obj, name, value)
            changed = True
    return changed


def _sync_lease(lease: models.Lease, worker: models.Worker) -> bool:
    if worker.status == c.WorkerStatus.BUSY.value:
        return _set(
            lease,
            status=c.LeaseStatus.ACTIVE.value,
            worker=worker.uuid,
            ipv4=worker.ipv4,
            ssh_user=worker.ssh_user,
        )
    return _set(
        lease,
        status=c.LeaseStatus.SCHEDULED.value,
        worker=worker.uuid,
    )


def _release(lease: models.Lease) -> None:
    lease.status = c.LeaseStatus.RELEASED.value
    lease.worker = None
    lease.ipv4 = None


class _PoolPlanner:
    """Builds the plan for one pool, one step at a time.

    The steps share the working state of the iteration (which workers are
    already condemned, which leases still wait), so they are methods of
    one object and run in the order of `plan`.
    """

    def __init__(
        self,
        pool: models.Pool,
        leases: list[models.Lease],
        workers: list[models.Worker],
        now: datetime.datetime,
    ) -> None:
        self._pool = pool
        self._leases = leases
        self._workers = workers
        self._now = now
        self._plan = PoolPlan()
        self._obsolete_ids: set = set()
        self._workers_by_lease = {w.lease: w for w in workers if w.lease is not None}
        # Leases that stay alive after this iteration.
        self._live_leases: set = set()
        # Live leases that still wait for a worker, oldest first.
        self._pending: list[models.Lease] = []
        # Free workers by readiness, oldest first, and the pool size.
        self._ready: list[models.Worker] = []
        self._provisioning: list[models.Worker] = []
        self._total = 0

    def plan(self) -> PoolPlan:
        self._advance_leases()
        self._collect_garbage()
        self._sort_free_workers()
        self._bind_pending_leases()
        self._keep_hot_pool()
        return self._plan

    def _destroy(self, worker: models.Worker) -> None:
        if worker.uuid not in self._obsolete_ids:
            self._obsolete_ids.add(worker.uuid)
            self._plan.obsolete_workers.append(worker)

    def _create_worker(self) -> models.Worker:
        worker = models.Worker.from_pool(self._pool)
        self._plan.new_workers.append(worker)
        return worker

    # 1. Lease lifecycle: release, requeue after a worker failure, follow
    # the bound worker's state.

    def _advance_leases(self) -> None:
        for lease in sorted(self._leases, key=lambda lease: lease.created_at):
            self._advance_lease(lease)

    def _advance_lease(self, lease: models.Lease) -> None:
        worker = self._workers_by_lease.get(lease.uuid)

        if not lease.release_requested and lease.is_expired(self._now):
            LOG.info("Lease %s expired, releasing it", lease.uuid)
            lease.release_requested = True

        if lease.release_requested:
            if worker is not None:
                self._destroy(worker)
            _release(lease)
            _mark(self._plan.leases, lease)
            return

        self._live_leases.add(lease.uuid)

        if worker is None:
            if _set(lease, status=c.LeaseStatus.NEW.value, worker=None, ipv4=None):
                _mark(self._plan.leases, lease)
            self._pending.append(lease)
        elif worker.status == c.WorkerStatus.ERROR.value:
            self._fail_lease(lease, worker)
        elif _sync_lease(lease, worker):
            _mark(self._plan.leases, lease)

    def _fail_lease(self, lease: models.Lease, worker: models.Worker) -> None:
        """The lease's worker failed: requeue the lease or give up on it."""
        self._destroy(worker)
        lease.attempts += 1
        lease.worker = None
        lease.ipv4 = None
        if lease.attempts >= c.MAX_LEASE_ATTEMPTS:
            lease.status = c.LeaseStatus.ERROR.value
            lease.error = f"{lease.attempts} workers failed to start for the lease"
        else:
            LOG.warning(
                "Worker %s of lease %s failed, requeueing the lease",
                worker.uuid,
                lease.uuid,
            )
            lease.status = c.LeaseStatus.NEW.value
        _mark(self._plan.leases, lease)

    # 2. Garbage: a worker that served a lease which is gone or final, a
    # worker that once got a key, a failed free worker, a free worker with
    # an outdated flavor or created before the last purge.

    def _collect_garbage(self) -> None:
        for worker in self._workers:
            if self._is_garbage(worker):
                self._destroy(worker)

    def _is_garbage(self, worker: models.Worker) -> bool:
        if worker.lease is not None:
            return worker.lease not in self._live_leases
        if worker.ssh_public_key is not None:
            return True
        if worker.status == c.WorkerStatus.ERROR.value:
            return True
        # Whoever changed the pool (the API, the core-agent), its free
        # workers are replaced with the new flavor.
        if not worker.has_flavor_of(self._pool):
            return True
        purge_before = self._pool.purge_before
        return purge_before is not None and worker.created_at < purge_before

    def _sort_free_workers(self) -> None:
        alive = [w for w in self._workers if w.uuid not in self._obsolete_ids]
        self._total = len(alive)
        free = [w for w in alive if w.is_free()]
        self._ready = sorted(
            (w for w in free if w.status == c.WorkerStatus.READY.value),
            key=lambda w: w.ready_at or w.created_at,
        )
        self._provisioning = sorted(
            (w for w in free if w.status != c.WorkerStatus.READY.value),
            key=lambda w: w.created_at,
        )

    # 3. Bind pending leases: a ready worker first, then one still booting,
    # then a new one if the pool may grow.

    def _bind_pending_leases(self) -> None:
        for lease in self._pending:
            worker = self._take_worker()
            if worker is None:
                LOG.warning(
                    "Pool %s reached max_workers=%d, lease %s waits",
                    self._pool.uuid,
                    self._pool.max_workers,
                    lease.uuid,
                )
                break
            worker.bind(lease)
            _sync_lease(lease, worker)
            _mark(self._plan.leases, lease)

    def _take_worker(self) -> models.Worker | None:
        if self._ready:
            worker = self._ready.pop(0)
            _mark(self._plan.workers, worker)
        elif self._provisioning:
            worker = self._provisioning.pop(0)
            _mark(self._plan.workers, worker)
        elif self._total < self._pool.max_workers:
            worker = self._create_worker()
            self._total += 1
        else:
            return None
        return worker

    # 4. Keep exactly hot_size free workers.

    def _keep_hot_pool(self) -> None:
        free_count = len(self._ready) + len(self._provisioning)
        while free_count < self._pool.hot_size and self._total < self._pool.max_workers:
            self._create_worker()
            free_count += 1
            self._total += 1

        surplus = free_count - self._pool.hot_size
        if surplus > 0:
            # Drop the least advanced workers first: booting, then the
            # newest ready ones.
            newest_first = list(reversed(self._provisioning)) + list(
                reversed(self._ready)
            )
            for worker in newest_first[:surplus]:
                self._destroy(worker)


def plan_pool(
    pool: models.Pool,
    leases: list[models.Lease],
    workers: list[models.Worker],
    now: datetime.datetime | None = None,
) -> PoolPlan:
    """Decide what to do with one pool.

    `leases` are the pool's leases that are not final yet, `workers` are
    all workers of the pool.
    """
    now = now or datetime.datetime.now(datetime.UTC)
    return _PoolPlanner(pool, leases, workers, now).plan()


class PoolSchedulerService(basic.BasicService):
    def _get_pools(self) -> list[models.Pool]:
        return models.Pool.objects.get_all()

    def _get_leases(self, pool: models.Pool) -> list[models.Lease]:
        return models.Lease.objects.get_all(
            filters={
                "pool": dm_filters.EQ(pool),
                "status": dm_filters.NotIn(tuple(c.FINAL_LEASE_STATUSES)),
            },
        )

    def _get_workers(self, pool: models.Pool) -> list[models.Worker]:
        return models.Worker.objects.get_all(filters={"pool": dm_filters.EQ(pool)})

    def _apply(self, plan: PoolPlan) -> None:
        # Destroy first: it frees the unique lease slot of a requeued
        # lease and drops references to released leases.
        for worker in plan.obsolete_workers:
            LOG.info("Destroying worker %s", worker.uuid)
            worker.delete()
        for worker in plan.new_workers:
            LOG.info("Creating worker %s", worker.uuid)
            worker.insert()
        for worker in plan.workers:
            worker.update()
        for lease in plan.leases:
            lease.update()

    def _schedule_pool(self, pool: models.Pool) -> None:
        with contexts.Context().session_manager():
            leases = self._get_leases(pool)
            workers = self._get_workers(pool)
            self._apply(plan_pool(pool, leases, workers))

    def _iteration(self) -> None:
        for pool in self._get_pools():
            try:
                self._schedule_pool(pool)
            except Exception:
                LOG.exception("Failed to schedule pool %s", pool.uuid)
