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

import collections

from gcl_iam import controllers as iam_controllers
from restalchemy.api import actions, constants
from restalchemy.api import controllers as ra_controllers
from restalchemy.api import field_permissions as field_p
from restalchemy.api import resources as ra_resources
from restalchemy.dm import filters as dm_filters

from exordos_opencode_server import constants as c
from exordos_opencode_server.controlplane.dm import models

RO = {constants.ALL: field_p.Permissions.RO}
HIDDEN = {constants.ALL: field_p.Permissions.HIDDEN}


class OpencodeController(ra_controllers.RoutesListController):
    """Controller for /v1/types/opencode/ endpoint"""

    __TARGET_PATH__ = f"/v1/types/{c.SLUG}/"


class VersionController(
    iam_controllers.PolicyBasedWithoutProjectController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """Controller for /v1/types/opencode/versions/ endpoint (read-only)"""

    __policy_service_name__ = c.POLICY_SERVICE_NAME
    __policy_name__ = "version"

    __resource__ = ra_resources.ResourceByRAModel(
        model_class=models.Version,
        convert_underscore=False,
        process_filters=True,
    )


class PoolController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """Controller for /v1/types/opencode/pools/ endpoint

    Pools are project resources: every call is scoped by the caller's
    project, and so is everything nested under a pool, since the parent is
    looked up through this controller.
    """

    __policy_service_name__ = c.POLICY_SERVICE_NAME
    __policy_name__ = "pool"

    __resource__ = ra_resources.ResourceByRAModel(
        model_class=models.Pool,
        convert_underscore=False,
        process_filters=True,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={"purge_before": RO},
        ),
    )

    @actions.post
    def purge_idle(self, resource: models.Pool):
        """Drop all free workers; the pool is refilled with its current flavor."""
        self._enforce("purge_idle")
        resource.request_purge()
        return resource

    @actions.get
    def stats(self, resource: models.Pool):
        """Count the pool's workers and leases by status."""
        self._enforce("read")
        workers = models.Worker.objects.get_all(
            filters={"pool": dm_filters.EQ(resource)},
        )
        leases = models.Lease.objects.get_all(
            filters={
                "pool": dm_filters.EQ(resource),
                "status": dm_filters.NotIn(tuple(c.FINAL_LEASE_STATUSES)),
            },
        )
        return {
            "hot_size": resource.hot_size,
            "max_workers": resource.max_workers,
            "free_workers": sum(1 for w in workers if w.is_free()),
            "workers": dict(collections.Counter(w.status for w in workers)),
            "leases": dict(collections.Counter(lease.status for lease in leases)),
        }


class WorkerController(
    iam_controllers.NestedPolicyBasedController,
    ra_controllers.BaseNestedResourceControllerPaginated,
):
    """Controller for /v1/types/opencode/pools/<uuid>/workers/ (read-only)"""

    __policy_service_name__ = c.POLICY_SERVICE_NAME
    __policy_name__ = "worker"
    __pr_name__ = "pool"

    __resource__ = ra_resources.ResourceByRAModel(
        model_class=models.Worker,
        convert_underscore=False,
        process_filters=True,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RO,
            # It may carry provider credentials.
            fields={"opencode_config": HIDDEN},
        ),
    )


class LeaseController(
    iam_controllers.NestedPolicyBasedController,
    ra_controllers.BaseNestedResourceControllerPaginated,
):
    """Controller for /v1/types/opencode/pools/<uuid>/leases/ endpoint

    A lease belongs to its pool's project. The pool is already looked up
    within the caller's project; every call is scoped by it once more, so
    the leases do not depend on how the parent was found.
    """

    __policy_service_name__ = c.POLICY_SERVICE_NAME
    __policy_name__ = "lease"
    __pr_name__ = "pool"

    __resource__ = ra_resources.ResourceByRAModel(
        model_class=models.Lease,
        convert_underscore=False,
        process_filters=True,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "pool": RO,
                "status": RO,
                "worker": RO,
                "ipv4": RO,
                "ssh_user": RO,
                "error": RO,
                "attempts": RO,
            },
        ),
    )

    def create(self, parent_resource, **kwargs):
        # The lease goes to its pool's project, also for a token without a
        # project. A foreign project_id in the request is rejected.
        kwargs.setdefault("project_id", parent_resource.project_id)
        self._enforce_and_override_project_id_in_kwargs("create", kwargs)
        return super(iam_controllers.PolicyBasedControllerMixin, self).create(
            parent_resource=parent_resource, **kwargs
        )

    def get(self, **kwargs):
        self._enforce_and_override_project_id_in_kwargs("read", kwargs)
        return super(iam_controllers.PolicyBasedControllerMixin, self).get(**kwargs)

    def filter(self, parent_resource, filters, order_by=None):
        self._enforce_and_override_project_id_in_kwargs("read", filters)
        return super(iam_controllers.NestedPolicyBasedController, self).filter(
            parent_resource=parent_resource, filters=filters, order_by=order_by
        )

    def delete(self, parent_resource, uuid):
        filters = {}
        self._enforce_and_override_project_id_in_kwargs("delete", filters)
        lease = super(iam_controllers.PolicyBasedControllerMixin, self).get(
            parent_resource=parent_resource, uuid=uuid, **filters
        )
        if lease.is_final():
            lease.delete()
        else:
            # The scheduler destroys the worker, then the lease is final.
            lease.request_release()

    @actions.post
    def release(self, resource: models.Lease):
        """Give the worker back: it is destroyed, never reused."""
        self._enforce("release")
        # Only the request is recorded here: the scheduler owns the status
        # and destroys the worker on its next iteration.
        if not resource.is_final():
            resource.request_release()
        return resource
