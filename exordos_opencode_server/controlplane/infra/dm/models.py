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

"""IaaS view of a worker: what is materialized in Exordos Core for it."""

import json
import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra import constants as sdk_c
from gcl_sdk.infra.dm import models as sdk_models
from restalchemy.dm import models as ra_models
from restalchemy.dm import properties, types, types_dynamic

from exordos_opencode_server import constants as c
from exordos_opencode_server.controlplane.dm import models


class PlainSecretConstructor(
    types_dynamic.AbstractKindModel, ra_models.SimpleViewMixin
):
    KIND = "plain"


# TODO: this model belongs in the SDK and should be imported from there. It
# is not available yet, so it is copied here. Replace this copy with the SDK
# model as soon as it appears.
class SSHKey(
    ra_models.ModelWithRequiredUUID,
    ra_models.ModelWithProject,
    ra_models.ModelWithNameDesc,
    ua_models.TargetResourceKindAwareMixin,
):
    """A Core SSH key (`/v1/secret/ssh_keys/`) authorized on one node.

    Core's agent on the node appends `target_public_key` to the user's
    `authorized_keys` and reports the key ACTIVE once it is there; the key
    is removed together with the node.
    """

    __init_resource_status__ = c.SecretStatus.NEW.value

    status = properties.property(
        types.Enum([s.value for s in c.SecretStatus]),
        default=c.SecretStatus.NEW.value,
    )
    target = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(sdk_models.NodeTarget),
        ),
        required=True,
    )
    user = properties.property(types.String(min_length=1, max_length=64))
    authorized_keys = properties.property(
        types.String(min_length=1, max_length=256),
        default=".ssh/authorized_keys",
    )
    target_public_key = properties.property(
        types.String(max_length=16384),
        default="",
    )
    constructor = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(PlainSecretConstructor),
        ),
        default=PlainSecretConstructor,
    )

    @classmethod
    def get_resource_kind(cls) -> str:
        return c.SSH_KEY_KIND

    def get_resource_target_fields(self) -> tp.Collection[str]:
        return frozenset(
            (
                "uuid",
                "name",
                "description",
                "project_id",
                "constructor",
                "user",
                "authorized_keys",
                "target",
                "target_public_key",
            )
        )


class Node(sdk_models.Node):
    """A Core node under the plugin's own resource kind."""

    @classmethod
    def get_resource_kind(cls) -> str:
        return c.NODE_KIND


class Config(sdk_models.Config):
    """A Core node config under the plugin's own resource kind."""

    @classmethod
    def get_resource_kind(cls) -> str:
        return c.CONFIG_KIND


class Worker(models.Worker, ua_models.InstanceWithDerivativesMixin):
    __derivative_model_map__ = {
        c.NODE_KIND: Node,
        c.CONFIG_KIND: Config,
        c.SSH_KEY_KIND: SSHKey,
    }

    @classmethod
    def get_resource_kind(cls) -> str:
        return "opencode_worker_iaas"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        return frozenset(
            (
                "uuid",
                "name",
                "cores",
                "ram",
                "disk_size",
                "image",
                "ssh_user",
                "opencode_config",
                "ssh_public_key",
            )
        )

    def get_resource_ignore_fields(self) -> tp.Collection[str]:
        # Runtime state written by the scheduler and the builder; it must
        # not make the worker look changed to the builder.
        return frozenset(("pool", "lease", "status", "ipv4", "ready_at", "error_at"))

    # Derivative identities are stable per worker so the builder always
    # emits the same resources.

    @property
    def opencode_config_uuid(self) -> sys_uuid.UUID:
        return sys_uuid.uuid5(self.uuid, "opencode-config")

    @property
    def ssh_key_uuid(self) -> sys_uuid.UUID:
        return sys_uuid.uuid5(self.uuid, "ssh-key")

    def build_node(self, project_id: sys_uuid.UUID) -> Node:
        return Node(
            uuid=self.uuid,
            name=self.name,
            hostname=self.name,
            project_id=project_id,
            cores=self.cores,
            ram=self.ram,
            disk_spec=sdk_models.RootDiskSpec(
                image=self.image,
                size=self.disk_size,
            ),
            status=sdk_c.NodeStatus.NEW.value,
        )

    def build_opencode_config(self, project_id: sys_uuid.UUID) -> Config | None:
        if not self.opencode_config:
            return None
        content = json.dumps(self.opencode_config, indent=2, sort_keys=True) + "\n"
        return Config(
            uuid=self.opencode_config_uuid,
            name=f"{self.name}-opencode-config",
            project_id=project_id,
            status=sdk_c.InstanceStatus.NEW.value,
            target=sdk_models.NodeTarget(node=self.uuid),
            body=sdk_models.TextBodyConfig(content=content),
            path=c.OPENCODE_CONFIG_PATH.format(user=self.ssh_user),
            owner=self.ssh_user,
            group=self.ssh_user,
            mode="0600",
            on_change=sdk_models.OnChangeNoAction(),
        )

    def build_ssh_key(self, project_id: sys_uuid.UUID) -> SSHKey | None:
        if not self.ssh_public_key:
            return None
        return SSHKey(
            uuid=self.ssh_key_uuid,
            name=f"{self.name}-lease-key",
            description="",
            project_id=project_id,
            target=sdk_models.NodeTarget(node=self.uuid),
            user=self.ssh_user,
            target_public_key=self.ssh_public_key,
        )
