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

"""The plugin's own infra agent.

The metapaas infra agent only materializes node sets and configs. Workers
are single Core nodes with their own configs and Core SSH keys, so the
plugin runs a second agent for them, next to its own scheduler that assigns
them to it. Both run inside the metapaas gservice; metapaas itself is
unchanged.

Schedulers assign target resources to agents by kind alone, across all
plugins. The plugin's kinds are therefore prefixed with its slug, so neither
the metapaas agent nor another plugin's agent can claim them.
"""

import os
import uuid as sys_uuid

from exordos_metapaas.common import constants as mc
from gcl_sdk.agents.universal import utils as ua_utils
from gcl_sdk.agents.universal.clients.orch import db as orch_db
from gcl_sdk.agents.universal.drivers import core as core_drivers
from gcl_sdk.agents.universal.services import agent as agent_service
from gcl_sdk.agents.universal.services import scheduler as scheduler_service

from exordos_opencode_server import constants as c

# Kept apart from the metapaas agent: each agent has its own payload and
# target fields storage.
WORK_DIR = os.path.join(mc.WORK_DIR, c.ELEMENT_NAME)


class OpencodeInfraScheduler(scheduler_service.UniversalAgentSchedulerService):
    def __init__(self, **kwargs):
        super().__init__(list(c.INFRA_AGENT_CAPABILITIES), **kwargs)


class OpencodeInfraAgent(agent_service.UniversalAgentService):
    def __init__(
        self,
        core_username: str,
        core_password: str,
        core_api_base_url: str,
        project_id: str | sys_uuid.UUID,
        work_dir: str = WORK_DIR,
        **kwargs,
    ):
        os.makedirs(work_dir, exist_ok=True)
        system_uuid = ua_utils.system_uuid()
        core_driver = core_drivers.RestCoreCapabilityDriver(
            username=core_username,
            password=core_password,
            user_api_base_url=core_api_base_url,
            project_id=project_id,
            agent_work_dir=work_dir,
            use_project_scope=True,
            **c.INFRA_AGENT_CAPABILITIES,
        )
        super().__init__(
            agent_uuid=sys_uuid.uuid5(system_uuid, c.INFRA_AGENT_NAME),
            orch_client=orch_db.DatabaseOrchClient(),
            caps_drivers=[core_driver],
            facts_drivers=[],
            payload_path=os.path.join(work_dir, "infra_opencode_agent_payload.json"),
            system_uuid=system_uuid,
            **kwargs,
        )
