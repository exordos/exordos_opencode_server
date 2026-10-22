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

import enum

# MetaPaaS plugin identity: the slug is the API mount point and table
# prefix, the element name is the Exordos element that registers the plugin.
SLUG = "opencode"
ELEMENT_NAME = "opencode_server"
POLICY_SERVICE_NAME = "exordos_opencode_server"

# The user the client logs in as. It is created in the worker image and
# owns the OpenCode configuration delivered by the control plane.
DEFAULT_SSH_USER = "ubuntu"

# Where the pool's OpenCode configuration lands on a worker.
OPENCODE_CONFIG_PATH = "/home/{user}/.config/opencode/opencode.json"

# A lease whose worker failed is put back into the queue this many times
# before it is given up with ERROR.
MAX_LEASE_ATTEMPTS = 3

# Name the plugin's own infra agent is derived from (uuid5 of the CP node
# system uuid). It must differ from the metapaas infra agent, which owns
# the node_set and config capabilities.
INFRA_AGENT_NAME = "opencode_server_infra_agent"

# Resource kinds of a worker's derivatives. The scheduler assigns target
# resources to agents by kind alone, across all plugins, so the kinds are
# prefixed with the plugin slug: another plugin's agent can then never pick
# up (or compete for) the plugin's resources.
NODE_KIND = f"{SLUG}_node"
CONFIG_KIND = f"{SLUG}_config"
SSH_KEY_KIND = f"{SLUG}_ssh_key"

# Core collections the plugin's infra agent materializes, by resource kind.
INFRA_AGENT_CAPABILITIES = {
    NODE_KIND: "/v1/compute/nodes/",
    CONFIG_KIND: "/v1/config/configs/",
    SSH_KEY_KIND: "/v1/secret/ssh_keys/",
}


class WorkerStatus(enum.StrEnum):
    # The worker row exists, its node is not requested yet.
    NEW = "NEW"
    # The node is being provisioned or configured.
    IN_PROGRESS = "IN_PROGRESS"
    # A hot worker: provisioned, configured and free.
    READY = "READY"
    # Bound to a lease, the client's key is being delivered.
    RESERVED = "RESERVED"
    # The client's key is on the node; the worker belongs to the lease.
    BUSY = "BUSY"
    ERROR = "ERROR"


# A worker in one of these states and without a lease can still be handed
# out: nobody has had access to it yet.
FREE_WORKER_STATUSES = frozenset(
    (
        WorkerStatus.NEW.value,
        WorkerStatus.IN_PROGRESS.value,
        WorkerStatus.READY.value,
    )
)


class LeaseStatus(enum.StrEnum):
    # Waiting for a worker.
    NEW = "NEW"
    # A worker is bound, the key is being delivered.
    SCHEDULED = "SCHEDULED"
    # The client may log in.
    ACTIVE = "ACTIVE"
    # The worker is destroyed. Kept for history.
    RELEASED = "RELEASED"
    ERROR = "ERROR"


FINAL_LEASE_STATUSES = frozenset(
    (
        LeaseStatus.RELEASED.value,
        LeaseStatus.ERROR.value,
    )
)


class SecretStatus(enum.StrEnum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"
