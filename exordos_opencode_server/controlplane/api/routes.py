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

from restalchemy.api import routes

from exordos_opencode_server.controlplane.api import controllers


class LeaseReleaseActionRoute(routes.Action):
    """Handler for .../leases/<uuid>/actions/release/invoke endpoint"""

    __controller__ = controllers.LeaseController


class LeaseRoute(routes.Route):
    __controller__ = controllers.LeaseController
    __allow_methods__ = [routes.CREATE, routes.FILTER, routes.GET, routes.DELETE]

    release = routes.action(LeaseReleaseActionRoute, invoke=True)


class WorkerRoute(routes.Route):
    __controller__ = controllers.WorkerController
    __allow_methods__ = [routes.FILTER, routes.GET]


class PoolPurgeIdleActionRoute(routes.Action):
    """Handler for /v1/types/opencode/pools/<uuid>/actions/purge_idle/invoke"""

    __controller__ = controllers.PoolController


class PoolStatsActionRoute(routes.Action):
    """Handler for /v1/types/opencode/pools/<uuid>/actions/stats endpoint"""

    __controller__ = controllers.PoolController


class PoolRoute(routes.Route):
    __controller__ = controllers.PoolController

    purge_idle = routes.action(PoolPurgeIdleActionRoute, invoke=True)
    stats = routes.action(PoolStatsActionRoute)

    # /v1/types/opencode/pools/<uuid>/workers/[<uuid>]
    workers = routes.route(WorkerRoute, resource_route=True)
    # /v1/types/opencode/pools/<uuid>/leases/[<uuid>]
    leases = routes.route(LeaseRoute, resource_route=True)


class VersionRoute(routes.Route):
    __controller__ = controllers.VersionController
    __allow_methods__ = [routes.FILTER, routes.GET]


class OpencodeRoute(routes.Route):
    """Handler for /v1/types/opencode/ endpoint (mounted by metapaas)."""

    __controller__ = controllers.OpencodeController
    __allow_methods__ = [routes.FILTER]

    # /v1/types/opencode/versions/[<uuid>]
    versions = routes.route(VersionRoute)
    # /v1/types/opencode/pools/[<uuid>]
    pools = routes.route(PoolRoute)
