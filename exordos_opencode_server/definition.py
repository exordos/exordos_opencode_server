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

import os

from exordos_metapaas.registry import PaaSDefinition

from exordos_opencode_server import constants as c


class OpencodeDefinition(PaaSDefinition):
    """Disposable OpenCode build workers served from a hot pool.

    Everything runs inside metapaas: the API is mounted under
    /v1/types/opencode/, and the gservice runs the pool scheduler, the
    worker builder, and the plugin's own infra agent with its scheduler
    (Core nodes and SSH keys). No PaaS layer and no dataplane agent: the
    worker is configured entirely through Core resources.
    """

    slug = c.SLUG
    element_name = c.ELEMENT_NAME

    def get_type_route(self):
        from exordos_opencode_server.controlplane.api import routes

        return routes.OpencodeRoute

    def get_migrations_path(self):
        return os.path.join(os.path.dirname(__file__), "migrations")

    def get_builders(self, core_username, core_password, core_api_base_url, project_id):
        from exordos_opencode_server.controlplane.infra.services import agent
        from exordos_opencode_server.controlplane.infra.services import (
            builder as infra_builder,
        )
        from exordos_opencode_server.controlplane.scheduler import service

        return [
            service.PoolSchedulerService(),
            infra_builder.WorkerInfraBuilder(project_id=project_id),
            agent.OpencodeInfraScheduler(),
            agent.OpencodeInfraAgent(
                core_username=core_username,
                core_password=core_password,
                core_api_base_url=core_api_base_url,
                project_id=project_id,
            ),
        ]

    def get_agent_models(self):
        return {
            "versions": "exordos_opencode_server.controlplane.dm.models:Version",
            "pools": "exordos_opencode_server.controlplane.dm.models:Pool",
        }

    def get_agent_filters(self):
        # Pools are global like versions: the EM project id in
        # `description` marks the ones declared by a manifest.
        return {
            "versions": "description",
            "pools": "description",
        }
