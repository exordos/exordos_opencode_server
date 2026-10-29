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

import json
import uuid as sys_uuid

import pytest
from gcl_sdk.infra.services import builder as sdk_builder

from exordos_opencode_server import constants as c
from exordos_opencode_server.controlplane.infra.dm import models as infra_models
from exordos_opencode_server.controlplane.infra.services import builder
from exordos_opencode_server.tests.unit.conftest import PUBLIC_KEY

PROJECT = sys_uuid.UUID(int=42)


@pytest.fixture
def worker(pool):
    worker = infra_models.Worker.from_pool(pool)
    worker.status = c.WorkerStatus.IN_PROGRESS.value
    return worker


@pytest.fixture
def infra_builder():
    return builder.WorkerInfraBuilder(project_id=PROJECT)


def actual(target, status, **fields):
    obj = target.__class__.restore_from_simple_view(
        skip_unknown_fields=True, **target.dump_to_simple_view()
    )
    obj.status = status
    for name, value in fields.items():
        setattr(obj, name, value)
    return obj


def collection(*pairs):
    return sdk_builder.InfraCollection(
        infra_objects=tuple(sdk_builder.InfraResourcePair(t, a) for t, a in pairs)
    )


def kinds(targets):
    return sorted(t.get_resource_kind() for t in targets)


def node_up(worker, ipv4="10.0.0.10"):
    node = worker.build_node(PROJECT)
    return node, actual(node, "ACTIVE", default_network={"ipv4": ipv4})


class TestInfraWorker:
    def test_node(self, worker):
        node = worker.build_node(PROJECT)
        assert node.uuid == worker.uuid
        assert node.project_id == PROJECT
        assert node.cores == worker.cores
        assert node.ram == worker.ram
        assert node.disk_spec.image == worker.image
        assert node.disk_spec.size == worker.disk_size

    def test_config(self, worker):
        config = worker.build_opencode_config(PROJECT)
        assert config.target.node == worker.uuid
        assert config.path == "/home/ubuntu/.config/opencode/opencode.json"
        assert config.owner == config.group == "ubuntu"
        assert json.loads(config.body.content) == worker.opencode_config

    def test_no_config_without_content(self, worker):
        worker.opencode_config = {}
        assert worker.build_opencode_config(PROJECT) is None

    def test_ssh_key(self, worker):
        assert worker.build_ssh_key(PROJECT) is None
        worker.ssh_public_key = PUBLIC_KEY
        key = worker.build_ssh_key(PROJECT)
        assert key.get_resource_kind() == "opencode_ssh_key"
        assert key.target.node == worker.uuid
        assert key.user == worker.ssh_user
        assert key.target_public_key == PUBLIC_KEY
        assert key.constructor.KIND == "plain"

    def test_derivative_uuids_are_stable(self, worker):
        assert worker.build_opencode_config(PROJECT).uuid == worker.opencode_config_uuid
        worker.ssh_public_key = PUBLIC_KEY
        assert worker.build_ssh_key(PROJECT).uuid == worker.ssh_key_uuid
        assert worker.opencode_config_uuid != worker.ssh_key_uuid

    def test_runtime_state_does_not_change_target(self, worker):
        before = worker.to_ua_resource().hash
        worker.status = c.WorkerStatus.READY.value
        worker.ipv4 = "10.0.0.10"
        assert worker.to_ua_resource().hash == before

    def test_binding_changes_target(self, worker):
        before = worker.to_ua_resource().hash
        worker.ssh_public_key = PUBLIC_KEY
        assert worker.to_ua_resource().hash != before


class TestWorkerInfraBuilder:
    def test_create_requests_only_node(self, infra_builder, worker):
        targets = infra_builder.create_infra(worker)
        assert kinds(targets) == ["opencode_node"]
        assert worker.status == c.WorkerStatus.IN_PROGRESS.value

    def test_node_booting(self, infra_builder, worker):
        node = worker.build_node(PROJECT)
        targets = infra_builder.actualize_infra(
            worker, collection((node, actual(node, "IN_PROGRESS")))
        )
        assert kinds(targets) == ["opencode_node"]
        assert worker.status == c.WorkerStatus.IN_PROGRESS.value

    def test_node_failed(self, infra_builder, worker):
        node = worker.build_node(PROJECT)
        infra_builder.actualize_infra(worker, collection((node, actual(node, "ERROR"))))
        assert worker.status == c.WorkerStatus.ERROR.value

    def test_node_without_address(self, infra_builder, worker):
        node = worker.build_node(PROJECT)
        targets = infra_builder.actualize_infra(
            worker, collection((node, actual(node, "ACTIVE", default_network={})))
        )
        assert kinds(targets) == ["opencode_node"]
        assert worker.status == c.WorkerStatus.IN_PROGRESS.value

    def test_node_up_requests_config(self, infra_builder, worker):
        targets = infra_builder.actualize_infra(worker, collection(node_up(worker)))
        assert kinds(targets) == ["opencode_config", "opencode_node"]
        assert worker.ipv4 == "10.0.0.10"
        assert worker.status == c.WorkerStatus.IN_PROGRESS.value

    def test_ready_once_config_delivered(self, infra_builder, worker):
        config = worker.build_opencode_config(PROJECT)
        infra_builder.actualize_infra(
            worker,
            collection(node_up(worker), (config, actual(config, "ACTIVE"))),
        )
        assert worker.status == c.WorkerStatus.READY.value
        assert worker.ready_at is not None

    def test_ready_without_config(self, infra_builder, worker):
        worker.opencode_config = {}
        targets = infra_builder.actualize_infra(worker, collection(node_up(worker)))
        assert kinds(targets) == ["opencode_node"]
        assert worker.status == c.WorkerStatus.READY.value

    def test_bound_worker_requests_key(self, infra_builder, worker):
        worker.ssh_public_key = PUBLIC_KEY
        config = worker.build_opencode_config(PROJECT)
        targets = infra_builder.actualize_infra(
            worker,
            collection(node_up(worker), (config, actual(config, "ACTIVE"))),
        )
        assert kinds(targets) == [
            "opencode_config",
            "opencode_node",
            "opencode_ssh_key",
        ]
        assert worker.status == c.WorkerStatus.RESERVED.value

    def test_busy_once_key_delivered(self, infra_builder, worker):
        worker.ssh_public_key = PUBLIC_KEY
        config = worker.build_opencode_config(PROJECT)
        key = worker.build_ssh_key(PROJECT)
        infra_builder.actualize_infra(
            worker,
            collection(
                node_up(worker),
                (config, actual(config, "ACTIVE")),
                (key, actual(key, "ACTIVE")),
            ),
        )
        assert worker.status == c.WorkerStatus.BUSY.value

    def test_key_failure(self, infra_builder, worker):
        worker.ssh_public_key = PUBLIC_KEY
        config = worker.build_opencode_config(PROJECT)
        key = worker.build_ssh_key(PROJECT)
        infra_builder.actualize_infra(
            worker,
            collection(
                node_up(worker),
                (config, actual(config, "ACTIVE")),
                (key, actual(key, "ERROR")),
            ),
        )
        assert worker.status == c.WorkerStatus.ERROR.value


class TestProvisionedWorker:
    """A worker whose node has been up: a node flap must not cut it off."""

    @pytest.fixture
    def busy(self, worker):
        worker.ssh_public_key = PUBLIC_KEY
        worker.ipv4 = "10.0.0.10"
        worker.status = c.WorkerStatus.BUSY.value
        return worker

    def delivered(self, worker, node_status, **node_fields):
        node = worker.build_node(PROJECT)
        config = worker.build_opencode_config(PROJECT)
        key = worker.build_ssh_key(PROJECT)
        return collection(
            (node, actual(node, node_status, **node_fields)),
            (config, actual(config, "ACTIVE")),
            (key, actual(key, "ACTIVE")),
        )

    def test_node_flap_keeps_config_and_key(self, infra_builder, busy):
        targets = infra_builder.actualize_infra(
            busy, self.delivered(busy, "IN_PROGRESS")
        )
        assert kinds(targets) == [
            "opencode_config",
            "opencode_node",
            "opencode_ssh_key",
        ]
        assert busy.status == c.WorkerStatus.BUSY.value
        assert busy.error_at is None

    def test_node_error_keeps_config_and_key(self, infra_builder, busy):
        targets = infra_builder.actualize_infra(busy, self.delivered(busy, "ERROR"))
        assert kinds(targets) == [
            "opencode_config",
            "opencode_node",
            "opencode_ssh_key",
        ]
        assert busy.status == c.WorkerStatus.ERROR.value
        assert busy.error_at is not None

    def test_error_keeps_its_first_timestamp(self, infra_builder, busy):
        infra_builder.actualize_infra(busy, self.delivered(busy, "ERROR"))
        first = busy.error_at
        infra_builder.actualize_infra(busy, self.delivered(busy, "ERROR"))
        assert busy.error_at == first

    def test_recovery_clears_error(self, infra_builder, busy):
        infra_builder.actualize_infra(busy, self.delivered(busy, "ERROR"))
        infra_builder.actualize_infra(
            busy,
            self.delivered(busy, "ACTIVE", default_network={"ipv4": "10.0.0.10"}),
        )
        assert busy.status == c.WorkerStatus.BUSY.value
        assert busy.error_at is None

    def test_node_gone_is_error(self, infra_builder, busy):
        config = busy.build_opencode_config(PROJECT)
        targets = infra_builder.actualize_infra(
            busy, collection((config, actual(config, "ACTIVE")))
        )
        assert "opencode_ssh_key" in kinds(targets)
        assert busy.status == c.WorkerStatus.ERROR.value

    def test_booting_node_error_sets_timestamp(self, infra_builder, worker):
        node = worker.build_node(PROJECT)
        infra_builder.actualize_infra(worker, collection((node, actual(node, "ERROR"))))
        assert worker.error_at is not None
