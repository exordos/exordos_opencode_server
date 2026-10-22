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

"""Element regression tests: the manifest and the build descriptor."""

import json
import pathlib
import re

import jinja2
import pytest
import yaml

from exordos_opencode_server import constants as c
from exordos_opencode_server.controlplane.api import controllers

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXORDOS_DIR = ROOT / "exordos"
METAPAAS_PROJECT = "4d657461-0000-0000-0000-000000000002"
METAPAAS_USER = "4d657461-0000-0000-0000-000000000001"
OWNER_ROLE = "726f6c65-0000-0000-0000-000000000002"
# Fixed UUIDs of Core's secret.ssh_key.* permissions.
CORE_SSH_KEY_PERMISSIONS = {
    "read": "42079bc1-48b3-4ffd-8131-35d1e4e5be05",
    "create": "e491736f-2749-42f7-ad39-bea4362cc38e",
    "update": "de7298aa-45d0-442f-96a2-efc84a40c56d",
    "delete": "23102ef6-c168-4490-8cd1-4b1a6903964b",
}

# Every action the controllers enforce, per policy.
ENFORCED = {
    "version": {"read"},
    "pool": {"create", "read", "update", "delete", "purge_idle"},
    "worker": {"read"},
    "lease": {"create", "read", "delete", "release"},
}


def render(name, **kwargs):
    template = (EXORDOS_DIR / "manifests" / name).read_text()
    rendered = jinja2.Template(template, undefined=jinja2.StrictUndefined).render(
        version="0.0.0", **kwargs
    )
    return yaml.safe_load(rendered)


@pytest.fixture(scope="module")
def manifest():
    return render(
        "opencode_server.yaml.j2",
        artifacts={"pypi_package": "urn:artifacts:wheel"},
        images={"opencode_worker": "urn:images:worker"},
    )


@pytest.fixture(scope="module")
def build():
    return yaml.safe_load((EXORDOS_DIR / "exordos.yaml").read_text())["build"]


def test_identity(manifest):
    assert manifest["name"] == c.ELEMENT_NAME
    assert set(manifest["requirements"]) == {"core", "metapaas"}


def test_raw_template_is_valid_yaml():
    # `exordos build` reads the element name from the unrendered template,
    # so it must stay valid YAML: no Jinja statements, only expressions.
    template = (EXORDOS_DIR / "manifests" / "opencode_server.yaml.j2").read_text()
    assert "{%" not in template
    assert yaml.safe_load(template)["name"] == c.ELEMENT_NAME


def test_registers_plugin(manifest):
    (slug, paas_type), *rest = manifest["resources"]["$metapaas.types"].items()
    assert not rest
    assert slug == c.SLUG
    assert paas_type["name"] == c.SLUG
    assert paas_type["element_name"] == c.ELEMENT_NAME
    assert paas_type["package"] == "urn:artifacts:wheel"
    assert paas_type["project_id"] == METAPAAS_PROJECT


def test_permissions_match_controllers(manifest):
    names = {p["name"] for p in manifest["resources"]["$core.iam.permissions"].values()}
    expected = {
        f"{c.POLICY_SERVICE_NAME}.{policy}.{action}"
        for policy, actions in ENFORCED.items()
        for action in actions
    }
    assert names == expected
    for controller in (
        controllers.VersionController,
        controllers.PoolController,
        controllers.WorkerController,
        controllers.LeaseController,
    ):
        assert controller.__policy_name__ in ENFORCED


def _granted(manifest, role):
    """The permissions bound to one of the element's roles."""
    bindings = manifest["resources"]["$core.iam.permissionbinding"].values()
    return {
        b["permission"]
        for b in bindings
        if b["role"] == f"$core.iam.roles.${role}:uuid"
    }


def _local(*keys):
    return {f"$core.iam.permissions.${key}:uuid" for key in keys}


def test_nothing_is_bound_to_the_owner_role(manifest):
    # Core grants a role's permissions in every project the role is held
    # in, and every project owner holds the owner role: binding to it would
    # hand the plugin to every project owner.
    bindings = manifest["resources"]["$core.iam.permissionbinding"].values()
    roles = set(manifest["resources"]["$core.iam.roles"])
    assert roles == {"opencode_pool_admin", "opencode_lease_client", "opencode_infra"}
    for binding in bindings:
        assert binding["role"] != OWNER_ROLE
        assert binding["role"] in {f"$core.iam.roles.${r}:uuid" for r in roles}


def test_pool_admin_role_has_every_permission(manifest):
    permissions = manifest["resources"]["$core.iam.permissions"]
    assert _granted(manifest, "opencode_pool_admin") == _local(*permissions)


def test_lease_client_role(manifest):
    assert _granted(manifest, "opencode_lease_client") == _local(
        "version_read",
        "pool_read",
        "lease_create",
        "lease_read",
        "lease_delete",
        "lease_release",
    )


def test_infra_role_manages_core_ssh_keys(manifest):
    # The infra agent writes /v1/secret/ssh_keys/ as the metapaas user,
    # whose owner role lacks secret.ssh_key.* in Core.
    assert _granted(manifest, "opencode_infra") == set(
        CORE_SSH_KEY_PERMISSIONS.values()
    )


def test_metapaas_user_roles(manifest):
    # Only the infra agent calls Core as the metapaas user; the other
    # services work on the database.
    assert manifest["resources"]["$core.iam.rolebinding"] == {
        "metapaas_opencode_infra": {
            "role": "$core.iam.roles.$opencode_infra:uuid",
            "user": METAPAAS_USER,
            "project": METAPAAS_PROJECT,
        }
    }


def test_version_catalog(manifest):
    versions = manifest["resources"]["$opencode_server.types.opencode.versions"]
    assert versions == {
        "default": {
            "name": "default",
            "image": "urn:images:worker",
        }
    }
    assert manifest["exports"]["version"]["link"] == (
        "$opencode_server.types.opencode.versions.$default"
    )


def test_exports_roles(manifest):
    exports = manifest["exports"]
    assert exports["lease_client_role"] == {
        "link": "$core.iam.roles.$opencode_lease_client"
    }
    assert exports["pool_admin_role"] == {
        "link": "$core.iam.roles.$opencode_pool_admin"
    }
    assert set(exports) == {"version", "lease_client_role", "pool_admin_role"}


def test_build_descriptor(build):
    (element,) = build["elements"]
    assert element["manifest"] == "manifests/opencode_server.yaml.j2"
    (artifact,) = element["artifacts"]
    assert artifact["name"] == "pypi_package"
    assert (EXORDOS_DIR / artifact["script"]).is_file()
    (image,) = element["images"]
    assert image["name"] == "opencode_worker"
    assert (EXORDOS_DIR / image["script"]).is_file()


def test_worker_image_pins_downloads():
    script = (EXORDOS_DIR / "images" / "worker_install.sh").read_text()
    for name in ("OPENCODE", "EXORDOS", "PACKER"):
        assert re.search(rf'^{name}_VERSION="[0-9.]+"$', script, re.MULTILINE)
        assert re.search(rf'^{name}_SHA256="[0-9a-f]{{64}}"$', script, re.MULTILINE)
    assert "sha256sum --check --strict" in script


def test_worker_image_preinstalls_pinned_packer_plugins():
    script = (EXORDOS_DIR / "images" / "worker_install.sh").read_text()
    for name, source in (
        ("SSHKEY", "github.com/ivoronin/sshkey"),
        ("QEMU", "github.com/hashicorp/qemu"),
    ):
        assert re.search(
            rf'^PACKER_{name}_PLUGIN_VERSION="[0-9.]+"$', script, re.MULTILINE
        )
        assert f"packer plugins install \\\n    {source} " in script


def test_opencode_runtime_config():
    text = (ROOT / "etc" / "opencode.jsonc").read_text()
    config = json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE))
    assert config["autoupdate"] is False
    assert config["share"] == "disabled"
