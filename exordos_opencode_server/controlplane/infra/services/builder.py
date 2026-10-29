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
import logging
import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra import constants as sdk_c
from gcl_sdk.infra.services import builder

from exordos_opencode_server import constants as c
from exordos_opencode_server.controlplane.infra.dm import models

LOG = logging.getLogger(__name__)

ACTIVE = sdk_c.InstanceStatus.ACTIVE.value
ERROR = sdk_c.InstanceStatus.ERROR.value


class WorkerInfraBuilder(builder.CoreInfraBuilder):
    """Materializes workers in Exordos Core and derives their status.

    Every worker is one Core node. Once the node is up, the builder adds
    the pool's OpenCode config and, after the worker is bound to a lease,
    the client's SSH key. The worker's status follows what Core reports:

    - node not active yet: IN_PROGRESS;
    - node active, config delivered, no key: READY (a hot worker);
    - key requested but not on the node yet: RESERVED;
    - key on the node: BUSY;
    - anything failed: ERROR.

    Once the node has been up (the worker has an address), its status is
    no longer a reason to change the target: Core may report a node that
    is up as IN_PROGRESS for a moment, and withdrawing the config and the
    key would cut the client off a live worker. Only ERROR counts then,
    and the scheduler decides whether it lasted long enough to matter.

    The builder only declares the target state; the infra agents apply it
    to Core and bring the actual state back.
    """

    def __init__(
        self,
        project_id: sys_uuid.UUID,
        instance_model: type[models.Worker] = models.Worker,
        **kwargs,
    ):
        super().__init__(instance_model, **kwargs)
        self._project_id = project_id

    def create_infra(
        self, instance: models.Worker
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        return self.actualize_infra(instance, builder.InfraCollection(infra_objects=()))

    def actualize_infra(
        self,
        instance: models.Worker,
        infra: builder.InfraCollection,
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        actuals = {
            pair.target.get_resource_kind(): pair.actual for pair in infra.infra_objects
        }

        node = instance.build_node(self._project_id)
        node_actual = actuals.get(c.NODE_KIND)
        node_status = None if node_actual is None else node_actual.status

        if node_status == ACTIVE:
            ipv4 = (node_actual.default_network or {}).get("ipv4")
            if ipv4:
                instance.ipv4 = ipv4

        if instance.ipv4 is None:
            # The node has never been up: configs and keys target the
            # node, so they wait for it.
            if node_status == ERROR:
                self._set_status(instance, c.WorkerStatus.ERROR.value)
            else:
                if node_status == ACTIVE:
                    LOG.info(
                        "Worker %s node is active without an address yet",
                        instance.uuid,
                    )
                self._set_status(instance, c.WorkerStatus.IN_PROGRESS.value)
            return (node,)

        targets = [node]
        config = instance.build_opencode_config(self._project_id)
        if config is not None:
            targets.append(config)
        ssh_key = instance.build_ssh_key(self._project_id)
        if ssh_key is not None:
            targets.append(ssh_key)

        if node_status in (ERROR, None):
            # Failed or gone from Core after it has been up.
            status = c.WorkerStatus.ERROR.value
        else:
            status = self._derive_status(
                config=config,
                config_actual=actuals.get(c.CONFIG_KIND),
                ssh_key=ssh_key,
                ssh_key_actual=actuals.get(c.SSH_KEY_KIND),
            )
        self._set_status(instance, status)
        if instance.status == c.WorkerStatus.READY.value and instance.ready_at is None:
            instance.ready_at = datetime.datetime.now(datetime.UTC)

        return tuple(targets)

    @staticmethod
    def _set_status(instance: models.Worker, status: str) -> None:
        instance.status = status
        if status != c.WorkerStatus.ERROR.value:
            instance.error_at = None
        elif instance.error_at is None:
            instance.error_at = datetime.datetime.now(datetime.UTC)

    @staticmethod
    def _derive_status(config, config_actual, ssh_key, ssh_key_actual) -> str:
        for actual in (config_actual, ssh_key_actual):
            if actual is not None and actual.status == ERROR:
                return c.WorkerStatus.ERROR.value

        if config is not None and (
            config_actual is None or config_actual.status != ACTIVE
        ):
            return c.WorkerStatus.IN_PROGRESS.value

        if ssh_key is None:
            return c.WorkerStatus.READY.value

        if ssh_key_actual is not None and ssh_key_actual.status == ACTIVE:
            return c.WorkerStatus.BUSY.value

        return c.WorkerStatus.RESERVED.value
