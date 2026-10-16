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
import uuid

import pytest

from exordos_opencode_server.controlplane.dm import models

# Fixed, obviously fake key material: tests never touch real credentials.
PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestTestTestTestTestTestTestTest test"
)
PROJECT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)


@pytest.fixture
def pool():
    return models.Pool(
        name="default",
        image="urn:images:test-image",
        cores=2,
        ram=4096,
        disk_size=20,
        hot_size=2,
        max_workers=5,
        opencode_config={"autoupdate": False},
    )


@pytest.fixture
def make_lease(pool):
    counter = iter(range(1000))

    def make(**kwargs):
        kwargs.setdefault("name", "lease")
        kwargs.setdefault("project_id", PROJECT_ID)
        kwargs.setdefault("ssh_public_key", PUBLIC_KEY)
        kwargs.setdefault(
            "created_at", NOW - datetime.timedelta(minutes=100 - next(counter))
        )
        return models.Lease(pool=pool, **kwargs)

    return make


@pytest.fixture
def make_worker(pool):
    counter = iter(range(1000))

    def make(status="READY", **kwargs):
        worker = models.Worker.from_pool(pool)
        worker.status = status
        worker.properties["created_at"].set_value_force(
            kwargs.pop(
                "created_at", NOW - datetime.timedelta(minutes=100 - next(counter))
            )
        )
        if status == "READY":
            kwargs.setdefault("ready_at", worker.created_at)
        for name, value in kwargs.items():
            setattr(worker, name, value)
        return worker

    return make
