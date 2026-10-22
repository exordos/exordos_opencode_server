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
        self._depends = []

    @property
    def migration_id(self):
        return "6f70656e-636f-6465-0000-000000000001"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        expressions = [
            """
            CREATE TABLE opencode_versions (
                uuid UUID PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                image VARCHAR(255) NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );
            """,
            """
            CREATE TABLE opencode_pools (
                uuid UUID PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                project_id UUID NOT NULL,
                image VARCHAR(255) NOT NULL,
                cores INT NOT NULL CHECK (cores BETWEEN 1 AND 128),
                ram INT NOT NULL CHECK (ram BETWEEN 1024 AND 1048576),
                disk_size INT NOT NULL CHECK (disk_size BETWEEN 10 AND 4096),
                hot_size INT NOT NULL CHECK (hot_size BETWEEN 0 AND 1024),
                max_workers INT NOT NULL CHECK (max_workers BETWEEN 1 AND 1024),
                ssh_user VARCHAR(64) NOT NULL,
                opencode_config JSONB NOT NULL DEFAULT '{}',
                purge_before TIMESTAMP,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                UNIQUE (project_id, name)
            );
            """,
            """
            CREATE TABLE opencode_leases (
                uuid UUID PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                project_id UUID NOT NULL,
                pool UUID NOT NULL
                    REFERENCES opencode_pools(uuid) ON DELETE CASCADE,
                ssh_public_key TEXT NOT NULL,
                expires_at TIMESTAMP,
                status VARCHAR(32) NOT NULL DEFAULT 'NEW',
                worker UUID,
                ipv4 VARCHAR(15),
                ssh_user VARCHAR(64),
                error VARCHAR(1024) NOT NULL DEFAULT '',
                attempts INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                release_requested BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );

            CREATE INDEX ON opencode_leases(project_id);
            CREATE INDEX ON opencode_leases(pool, status);
            """,
            """
            CREATE TABLE opencode_workers (
                uuid UUID PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                pool UUID NOT NULL
                    REFERENCES opencode_pools(uuid) ON DELETE CASCADE,
                status VARCHAR(32) NOT NULL DEFAULT 'NEW',
                cores INT NOT NULL CHECK (cores BETWEEN 1 AND 128),
                ram INT NOT NULL CHECK (ram BETWEEN 1024 AND 1048576),
                disk_size INT NOT NULL CHECK (disk_size BETWEEN 10 AND 4096),
                image VARCHAR(255) NOT NULL,
                ssh_user VARCHAR(64) NOT NULL,
                opencode_config JSONB NOT NULL DEFAULT '{}',
                lease UUID UNIQUE
                    REFERENCES opencode_leases(uuid) ON DELETE SET NULL,
                ssh_public_key TEXT,
                ipv4 VARCHAR(15),
                ready_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );

            CREATE INDEX ON opencode_workers(pool, status);
            """,
        ]

        for expression in expressions:
            session.execute(expression)

    def downgrade(self, session):
        tables = [
            "opencode_workers",
            "opencode_leases",
            "opencode_pools",
            "opencode_versions",
        ]

        for table in tables:
            self._delete_table_if_exists(session, table)


migration_step = MigrationStep()
