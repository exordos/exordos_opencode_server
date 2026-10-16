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
from restalchemy.common import exceptions as ra_e

from exordos_opencode_server import constants as c
from exordos_opencode_server.controlplane.dm import models
from exordos_opencode_server.tests.unit.conftest import NOW, PUBLIC_KEY


class TestSSHPublicKey:
    @pytest.mark.parametrize(
        "value",
        [
            PUBLIC_KEY,
            "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ",
            "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAI",
        ],
    )
    def test_valid(self, value):
        assert models.SSHPublicKey().validate(value)

    @pytest.mark.parametrize(
        "value",
        [
            "not-a-key AAAAC3NzaC1lZDI1NTE5AAAA",
            "ssh-ed25519",
            PUBLIC_KEY + "\nssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIother",
            "-----BEGIN OPENSSH PRIVATE KEY-----",
        ],
    )
    def test_invalid(self, value):
        assert not models.SSHPublicKey().validate(value)


class TestPool:
    def test_defaults(self):
        pool = models.Pool(name="p", image="urn:images:test-image")
        assert pool.hot_size == 1
        assert pool.ssh_user == c.DEFAULT_SSH_USER
        assert pool.opencode_config == {}
        assert pool.purge_before is None

    def test_update_never_writes_the_purge_request(self, pool):
        # The API owns the column; the core-agent writes the rest of the
        # row for pools declared in a manifest.
        data = pool.get_data_properties()
        assert "purge_before" not in data
        assert "opencode_config" in data

    def test_is_a_target_resource(self, pool):
        resource = pool.to_ua_resource("em_opencode_server_types_opencode_pools")
        assert resource.value["name"] == pool.name
        assert resource.value["cores"] == pool.cores


class TestLease:
    def test_defaults(self, make_lease):
        lease = make_lease()
        assert lease.status == c.LeaseStatus.NEW.value
        assert lease.worker is None
        assert lease.attempts == 0

    def test_rejects_bad_key(self, make_lease):
        with pytest.raises(ra_e.TypeError):
            make_lease(ssh_public_key="bad key value here")

    def test_is_expired(self, make_lease):
        assert not make_lease().is_expired(NOW)
        lease = make_lease(expires_at=NOW - datetime.timedelta(seconds=1))
        assert lease.is_expired(NOW)
        lease = make_lease(expires_at=NOW + datetime.timedelta(seconds=1))
        assert not lease.is_expired(NOW)

    def test_key_is_immutable(self, make_lease):
        lease = make_lease()
        lease.ssh_public_key = PUBLIC_KEY.replace("test", "other")
        with pytest.raises(models.ImmutableFieldError):
            lease.update()

    def test_pool_is_immutable(self, make_lease):
        lease = make_lease()
        lease.pool = models.Pool(name="other", image="urn:images:test-image")
        with pytest.raises(models.ImmutableFieldError):
            lease.update()

    def test_is_final(self, make_lease):
        assert not make_lease().is_final()
        assert make_lease(status="RELEASED").is_final()
        assert make_lease(status="ERROR").is_final()


class TestWorker:
    def test_from_pool_snapshots_flavor(self, pool):
        worker = models.Worker.from_pool(pool)
        assert worker.cores == pool.cores
        assert worker.ram == pool.ram
        assert worker.disk_size == pool.disk_size
        assert worker.image == pool.image
        assert worker.ssh_user == pool.ssh_user
        assert worker.opencode_config == pool.opencode_config
        assert worker.name == f"opencode-worker-{str(worker.uuid)[:8]}"

        # A later flavor change doesn't touch the worker.
        pool.cores = 8
        assert worker.cores == 2

    @pytest.mark.parametrize(
        "status,free",
        [
            ("NEW", True),
            ("IN_PROGRESS", True),
            ("READY", True),
            ("RESERVED", False),
            ("BUSY", False),
            ("ERROR", False),
        ],
    )
    def test_is_free_by_status(self, make_worker, status, free):
        assert make_worker(status=status).is_free() is free

    def test_worker_with_key_is_never_free(self, make_worker):
        assert not make_worker(ssh_public_key=PUBLIC_KEY).is_free()

    def test_bind_ready_worker(self, make_worker, make_lease):
        worker = make_worker()
        lease = make_lease()
        worker.bind(lease)
        assert worker.lease == lease.uuid
        assert worker.ssh_public_key == lease.ssh_public_key
        assert worker.status == c.WorkerStatus.RESERVED.value

    def test_bind_booting_worker_keeps_status(self, make_worker, make_lease):
        worker = make_worker(status="IN_PROGRESS")
        worker.bind(make_lease())
        assert worker.status == c.WorkerStatus.IN_PROGRESS.value


class TestLeaseRelease:
    def test_update_never_writes_the_release_request(self, make_lease):
        # The API owns the column; a whole-row update would overwrite a
        # request that arrived after the scheduler read the lease.
        data = make_lease().get_data_properties()
        assert "release_requested" not in data
        assert "status" in data

    def test_release_is_not_requested_by_default(self, make_lease):
        assert make_lease().release_requested is False
