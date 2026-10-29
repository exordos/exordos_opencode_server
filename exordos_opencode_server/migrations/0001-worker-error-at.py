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

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = ["0000-init-opencode.py"]

    @property
    def migration_id(self):
        return "6f70656e-636f-6465-0000-000000000002"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute("ALTER TABLE opencode_workers ADD COLUMN error_at TIMESTAMP;")

    def downgrade(self, session):
        session.execute("ALTER TABLE opencode_workers DROP COLUMN error_at;")


migration_step = MigrationStep()
