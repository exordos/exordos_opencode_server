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

import importlib
import os

from gcl_iam import controllers as iam_controllers
from restalchemy.api import routes as ra_routes

from exordos_opencode_server import constants as c
from exordos_opencode_server.controlplane.api import controllers, routes
from exordos_opencode_server.definition import OpencodeDefinition


class TestDefinition:
    def test_identity(self):
        definition = OpencodeDefinition()
        assert definition.slug == "opencode"
        assert definition.element_name == "opencode_server"

    def test_route(self):
        assert OpencodeDefinition().get_type_route() is routes.OpencodeRoute

    def test_migrations_path(self):
        path = OpencodeDefinition().get_migrations_path()
        assert os.path.isfile(os.path.join(path, "0000-init-opencode.py"))

    def test_agent_models_resolve(self):
        definition = OpencodeDefinition()
        models = definition.get_agent_models()
        for spec in models.values():
            module, _, name = spec.partition(":")
            assert hasattr(importlib.import_module(module), name)

    def test_agent_filters_none(self):
        # Without a filter the core-agent keeps to the rows it created from
        # manifests, in whatever project; a filter would also have it take
        # over the pools created through the API.
        assert OpencodeDefinition().get_agent_filters() == {}

    def test_infra_agent_owns_its_capabilities_only(self):
        # Kinds are global across plugins: keep ours prefixed with the slug,
        # so no other agent (metapaas' own included) competes for them.
        assert set(c.INFRA_AGENT_CAPABILITIES) == {
            "opencode_node",
            "opencode_config",
            "opencode_ssh_key",
        }
        assert all(k.startswith(f"{c.SLUG}_") for k in c.INFRA_AGENT_CAPABILITIES)


class TestApi:
    def test_routes(self):
        assert routes.OpencodeRoute.versions
        assert routes.OpencodeRoute.pools
        assert routes.PoolRoute.workers
        assert routes.PoolRoute.leases
        assert routes.PoolRoute.purge_idle
        assert routes.PoolRoute.stats
        assert routes.LeaseRoute.release

    def test_leases_have_no_update(self):
        assert ra_routes.UPDATE not in routes.LeaseRoute.__allow_methods__

    def test_versions_and_workers_are_read_only(self):
        for route in (routes.VersionRoute, routes.WorkerRoute):
            assert set(route.__allow_methods__) == {ra_routes.FILTER, ra_routes.GET}

    def test_pools_are_scoped_by_project(self):
        assert issubclass(
            controllers.PoolController, iam_controllers.PolicyBasedController
        )

    def test_policy_names(self):
        for controller, name in (
            (controllers.VersionController, "version"),
            (controllers.PoolController, "pool"),
            (controllers.WorkerController, "worker"),
            (controllers.LeaseController, "lease"),
        ):
            assert controller.__policy_service_name__ == c.POLICY_SERVICE_NAME
            assert controller.__policy_name__ == name
