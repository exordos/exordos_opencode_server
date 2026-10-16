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
METAPAAS_OWNER_ROLE = "726f6c65-0000-0000-0000-000000000002"
EM_PROJECT = "12345678-c625-4fee-81d5-f691897b8142"
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
def opencode_halogen_qwen38():
    return render("opencode_halogen_qwen38.yaml.j2")


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


def test_every_permission_is_bound(manifest):
    resources = manifest["resources"]
    permissions = resources["$core.iam.permissions"]
    bindings = resources["$core.iam.permissionbinding"]
    assert len(bindings) == len(permissions) + len(CORE_SSH_KEY_PERMISSIONS)
    for key in permissions:
        binding = bindings[f"{key}_binding"]
        assert binding["permission"] == f"$core.iam.permissions.${key}:uuid"
        assert binding["role"] == METAPAAS_OWNER_ROLE
        assert binding["project_id"] == METAPAAS_PROJECT


def test_metapaas_user_manages_core_ssh_keys(manifest):
    # The infra agent writes /v1/secret/ssh_keys/ as the metapaas user,
    # whose owner role lacks secret.ssh_key.* in Core.
    bindings = manifest["resources"]["$core.iam.permissionbinding"]
    for action, permission in CORE_SSH_KEY_PERMISSIONS.items():
        binding = bindings[f"core_ssh_key_{action}_binding"]
        assert binding["permission"] == permission
        assert binding["role"] == METAPAAS_OWNER_ROLE
        assert binding["project_id"] == METAPAAS_PROJECT


def test_version_catalog(manifest):
    versions = manifest["resources"]["$opencode_server.types.opencode.versions"]
    assert versions == {
        "default": {
            "name": "default",
            "description": EM_PROJECT,
            "image": "urn:images:worker",
        }
    }
    assert manifest["exports"]["version"]["link"] == (
        "$opencode_server.types.opencode.versions.$default"
    )


def test_exports_client_permissions(manifest):
    permissions = manifest["resources"]["$core.iam.permissions"]
    for name in ("lease_create", "lease_read", "lease_delete", "lease_release"):
        assert name in permissions
        assert manifest["exports"][name]["link"] == f"$core.iam.permissions.${name}"


def test_build_descriptor(build):
    pool_element, element = build["elements"]
    assert pool_element == {"manifest": "manifests/opencode_halogen_qwen38.yaml.j2"}
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


def test_opencode_runtime_config():
    text = (ROOT / "etc" / "opencode.jsonc").read_text()
    config = json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE))
    assert config["autoupdate"] is False
    assert config["share"] == "disabled"


def test_opencode_halogen_qwen38_requires_the_element(opencode_halogen_qwen38):
    assert opencode_halogen_qwen38["name"] == "opencode_halogen_qwen38"
    assert set(opencode_halogen_qwen38["requirements"]) == {"core", c.ELEMENT_NAME}
    assert opencode_halogen_qwen38["imports"]["version"] == {
        "element": f"${c.ELEMENT_NAME}",
        "kind": "resource",
        "link": f"${c.ELEMENT_NAME}.types.{c.SLUG}.versions.$default",
    }


def test_opencode_halogen_qwen38_pool(opencode_halogen_qwen38):
    pools = opencode_halogen_qwen38["resources"][
        f"${c.ELEMENT_NAME}.types.{c.SLUG}.pools"
    ]
    pool = pools["halogen_qwen38"]
    # The core-agent syncs only the pools marked with the EM project id.
    assert pool["description"] == EM_PROJECT
    assert pool["image"] == "$opencode_halogen_qwen38.imports.$version:image"
    assert "version" not in pool
    assert (pool["cores"], pool["ram"], pool["hot_size"]) == (6, 8192, 1)
    config = pool["opencode_config"]
    provider = config["provider"]["halogen"]
    assert config["model"] == "halogen/" + next(iter(provider["models"]))
    assert provider["options"] == {"baseURL": "$core.vs.variables.$base_url:value"}


def test_opencode_halogen_qwen38_endpoint_is_a_runtime_variable(
    opencode_halogen_qwen38,
):
    # No endpoint is baked into the published artifact.
    variable = opencode_halogen_qwen38["resources"]["$core.vs.variables"]["base_url"]
    assert variable["name"] == "opencode_halogen_qwen38_base_url"
    assert variable["project_id"] == EM_PROJECT
    assert variable["setter"] == {"kind": "selector", "selector_strategy": "latest"}
