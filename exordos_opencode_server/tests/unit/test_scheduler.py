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

import datetime

import pytest

from exordos_opencode_server import constants as c
from exordos_opencode_server.controlplane.scheduler import service
from exordos_opencode_server.tests.unit.conftest import NOW, PUBLIC_KEY


def plan(pool, leases=(), workers=()):
    return service.plan_pool(pool, list(leases), list(workers), now=NOW)


def uuids(items):
    return {i.uuid for i in items}


class TestHotPool:
    def test_fills_empty_pool(self, pool):
        result = plan(pool)
        assert len(result.new_workers) == pool.hot_size
        assert all(w.is_free() for w in result.new_workers)
        assert not result.obsolete_workers

    def test_counts_booting_workers_as_free(self, pool, make_worker):
        workers = [make_worker(status="IN_PROGRESS"), make_worker()]
        result = plan(pool, workers=workers)
        assert not result.new_workers
        assert not result.obsolete_workers

    def test_zero_hot_size_keeps_nothing(self, pool, make_worker):
        pool.hot_size = 0
        workers = [make_worker(), make_worker(status="NEW")]
        result = plan(pool, workers=workers)
        assert not result.new_workers
        assert uuids(result.obsolete_workers) == uuids(workers)

    def test_shrink_drops_least_advanced_first(self, pool, make_worker):
        pool.hot_size = 1
        old_ready = make_worker()
        new_ready = make_worker()
        booting = make_worker(status="IN_PROGRESS")
        result = plan(pool, workers=[old_ready, new_ready, booting])
        assert uuids(result.obsolete_workers) == {booting.uuid, new_ready.uuid}

    def test_respects_max_workers(self, pool, make_worker, make_lease):
        pool.max_workers = 3
        busy = [
            make_worker(status="BUSY", lease=lease.uuid, ssh_public_key=PUBLIC_KEY)
            for lease in (make_lease(), make_lease())
        ]
        leases = [make_lease(status="ACTIVE", worker=w.uuid) for w in busy]
        for worker, lease in zip(busy, leases, strict=True):
            worker.lease = lease.uuid
        result = plan(pool, leases=leases, workers=busy)
        assert len(result.new_workers) == 1

    def test_failed_free_worker_is_replaced(self, pool, make_worker):
        failed = make_worker(status="ERROR")
        ok = make_worker()
        result = plan(pool, workers=[failed, ok])
        assert uuids(result.obsolete_workers) == {failed.uuid}
        assert len(result.new_workers) == 1


class TestBinding:
    def test_binds_ready_worker_first(self, pool, make_worker, make_lease):
        booting = make_worker(status="IN_PROGRESS")
        ready = make_worker()
        lease = make_lease()
        result = plan(pool, leases=[lease], workers=[booting, ready])

        assert ready.lease == lease.uuid
        assert ready.ssh_public_key == lease.ssh_public_key
        assert ready.status == c.WorkerStatus.RESERVED.value
        assert lease.status == c.LeaseStatus.SCHEDULED.value
        assert lease.worker == ready.uuid
        assert ready in result.workers
        assert lease in result.leases
        # The pool is refilled: one free worker is left, hot_size is 2.
        assert len(result.new_workers) == 1

    def test_oldest_lease_and_oldest_ready_worker_first(
        self, pool, make_worker, make_lease
    ):
        pool.max_workers = 2
        first_worker, second_worker = make_worker(), make_worker()
        first_lease, second_lease = make_lease(), make_lease()
        plan(
            pool,
            leases=[second_lease, first_lease],
            workers=[second_worker, first_worker],
        )
        assert first_worker.lease == first_lease.uuid
        assert second_worker.lease == second_lease.uuid

    def test_binds_booting_worker_when_none_ready(self, pool, make_worker, make_lease):
        booting = make_worker(status="IN_PROGRESS")
        lease = make_lease()
        plan(pool, leases=[lease], workers=[booting])
        assert booting.lease == lease.uuid
        assert booting.status == c.WorkerStatus.IN_PROGRESS.value

    def test_creates_worker_on_demand(self, pool, make_lease):
        pool.hot_size = 0
        lease = make_lease()
        result = plan(pool, leases=[lease])
        assert len(result.new_workers) == 1
        worker = result.new_workers[0]
        assert worker.lease == lease.uuid
        assert worker.ssh_public_key == lease.ssh_public_key
        assert lease.worker == worker.uuid
        assert lease.status == c.LeaseStatus.SCHEDULED.value

    def test_lease_waits_at_max_workers(self, pool, make_lease):
        pool.hot_size = 0
        pool.max_workers = 1
        first, second = make_lease(), make_lease()
        result = plan(pool, leases=[first, second])
        assert len(result.new_workers) == 1
        assert first.status == c.LeaseStatus.SCHEDULED.value
        assert second.status == c.LeaseStatus.NEW.value
        assert second.worker is None


class TestLeaseLifecycle:
    def bound(
        self, make_worker, make_lease, worker_status, lease_status, **lease_kwargs
    ):
        lease = make_lease(status=lease_status, **lease_kwargs)
        worker = make_worker(
            status=worker_status,
            lease=lease.uuid,
            ssh_public_key=PUBLIC_KEY,
            ipv4="10.0.0.10",
        )
        lease.worker = worker.uuid
        return lease, worker

    def test_lease_becomes_active_with_worker(self, pool, make_worker, make_lease):
        lease, worker = self.bound(make_worker, make_lease, "BUSY", "SCHEDULED")
        result = plan(pool, leases=[lease], workers=[worker])
        assert lease.status == c.LeaseStatus.ACTIVE.value
        assert lease.ipv4 == "10.0.0.10"
        assert lease.ssh_user == worker.ssh_user
        assert lease in result.leases
        assert not result.obsolete_workers

    def test_unchanged_lease_is_not_saved(self, pool, make_worker, make_lease):
        lease, worker = self.bound(make_worker, make_lease, "RESERVED", "SCHEDULED")
        result = plan(pool, leases=[lease], workers=[worker])
        assert lease not in result.leases

    def test_release_destroys_worker(self, pool, make_worker, make_lease):
        lease, worker = self.bound(
            make_worker, make_lease, "BUSY", "ACTIVE", release_requested=True
        )
        result = plan(pool, leases=[lease], workers=[worker])
        assert lease.status == c.LeaseStatus.RELEASED.value
        assert lease.worker is None
        assert uuids(result.obsolete_workers) == {worker.uuid}

    def test_release_of_unbound_lease(self, pool, make_lease):
        lease = make_lease(status="ACTIVE", release_requested=True)
        result = plan(pool, leases=[lease])
        assert lease.status == c.LeaseStatus.RELEASED.value
        assert not result.obsolete_workers

    def test_expired_lease_is_released(self, pool, make_worker, make_lease):
        lease, worker = self.bound(make_worker, make_lease, "BUSY", "ACTIVE")
        lease.expires_at = NOW - datetime.timedelta(minutes=1)
        result = plan(pool, leases=[lease], workers=[worker])
        assert lease.status == c.LeaseStatus.RELEASED.value
        assert uuids(result.obsolete_workers) == {worker.uuid}

    def test_failed_worker_requeues_lease(self, pool, make_worker, make_lease):
        lease, worker = self.bound(make_worker, make_lease, "ERROR", "SCHEDULED")
        result = plan(pool, leases=[lease], workers=[worker])
        assert uuids(result.obsolete_workers) == {worker.uuid}
        assert lease.status == c.LeaseStatus.NEW.value
        assert lease.attempts == 1
        assert lease.worker is None
        # The failed worker is not handed the lease again in this iteration.
        assert all(w.lease != lease.uuid for w in result.new_workers)

    def test_lease_fails_after_max_attempts(self, pool, make_worker, make_lease):
        lease, worker = self.bound(make_worker, make_lease, "ERROR", "SCHEDULED")
        lease.attempts = c.MAX_LEASE_ATTEMPTS - 1
        plan(pool, leases=[lease], workers=[worker])
        assert lease.status == c.LeaseStatus.ERROR.value
        assert lease.error

    def test_lease_lost_worker_goes_back_to_queue(self, pool, make_worker, make_lease):
        lease = make_lease(status="ACTIVE", ipv4="10.0.0.10")
        ready = make_worker()
        plan(pool, leases=[lease], workers=[ready])
        assert lease.worker == ready.uuid
        assert lease.status == c.LeaseStatus.SCHEDULED.value


class TestGarbage:
    def test_worker_of_deleted_lease_is_destroyed(self, pool, make_worker):
        # The lease row is gone: the FK set the worker's lease to NULL.
        orphan = make_worker(status="BUSY", ssh_public_key=PUBLIC_KEY)
        result = plan(pool, workers=[orphan])
        assert uuids(result.obsolete_workers) == {orphan.uuid}

    def test_worker_of_final_lease_is_destroyed(self, pool, make_worker, make_lease):
        lease = make_lease(status="RELEASED")
        worker = make_worker(status="BUSY", lease=lease.uuid, ssh_public_key=PUBLIC_KEY)
        # Final leases are not passed to the planner.
        result = plan(pool, workers=[worker])
        assert uuids(result.obsolete_workers) == {worker.uuid}

    @pytest.mark.parametrize(
        "field, value",
        [
            ("cores", 6),
            ("ram", 8192),
            ("disk_size", 40),
            ("ssh_user", "builder"),
            ("opencode_config", {"model": "other/model"}),
        ],
    )
    def test_flavor_change_replaces_only_free_workers(
        self, pool, make_worker, make_lease, field, value
    ):
        lease = make_lease(status="ACTIVE")
        busy = make_worker(status="BUSY", lease=lease.uuid, ssh_public_key=PUBLIC_KEY)
        lease.worker = busy.uuid
        ready = make_worker()
        booting = make_worker(status="IN_PROGRESS")
        setattr(pool, field, value)

        result = plan(pool, leases=[lease], workers=[busy, ready, booting])

        assert uuids(result.obsolete_workers) == {ready.uuid, booting.uuid}
        assert len(result.new_workers) == pool.hot_size
        assert all(getattr(w, field) == value for w in result.new_workers)

    def test_new_image_replaces_free_workers(self, pool, make_worker):
        ready = make_worker()
        pool.image = "urn:images:next-image"

        result = plan(pool, workers=[ready])

        assert uuids(result.obsolete_workers) == {ready.uuid}
        assert {w.image for w in result.new_workers} == {"urn:images:next-image"}

    def test_unchanged_flavor_keeps_free_workers(self, pool, make_worker):
        workers = [make_worker(), make_worker()]
        result = plan(pool, workers=workers)
        assert not result.obsolete_workers
        assert not result.new_workers

    def test_purge_drops_only_free_workers_created_before(
        self, pool, make_worker, make_lease
    ):
        lease = make_lease(status="ACTIVE")
        busy = make_worker(status="BUSY", lease=lease.uuid, ssh_public_key=PUBLIC_KEY)
        lease.worker = busy.uuid
        old_free = make_worker()
        pool.purge_before = NOW - datetime.timedelta(minutes=10)
        new_free = make_worker(created_at=NOW - datetime.timedelta(minutes=5))

        result = plan(pool, leases=[lease], workers=[busy, old_free, new_free])

        assert uuids(result.obsolete_workers) == {old_free.uuid}
        # Refilled up to hot_size with the current flavor.
        assert len(result.new_workers) == 1
