from __future__ import annotations

import json
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).parents[1]
PROJECT_ID = "12345678-c625-4fee-81d5-f691897b8142"


def _render_manifest() -> dict[str, object]:
    source = (ROOT / "exordos/manifests/opencode_server.yaml.j2").read_text()
    rendered = (
        Environment(undefined=StrictUndefined)
        .from_string(source)
        .render(
            version="0.1.0",
            images={"opencode_server": "https://repo.example.invalid/image.raw.zst"},
        )
    )
    manifest = yaml.safe_load(rendered)
    assert isinstance(manifest, dict)
    return manifest


def test_build_downloads_a_pinned_upstream_release() -> None:
    config = yaml.safe_load((ROOT / "exordos/exordos.yaml").read_text())
    assert config["build"]["deps"][0]["path"]["src"] == (
        "../../exordos_opencode_server"
    )
    element = config["build"]["elements"][0]
    assert element["manifest"] == "manifests/opencode_server.yaml.j2"
    image = element["images"][0]
    assert image["name"] == "opencode_server"
    assert image["profile"] == "exordos_base"

    installer = (ROOT / "exordos/images/install.sh").read_text()
    assert 'OPENCODE_VERSION="1.18.31"' in installer
    assert 'OPENCODE_SHA256="b283e8db' in installer
    assert "github.com/anomalyco/opencode/releases/download/v" in installer
    assert "sha256sum --check --strict" in installer
    assert "/usr/local/bin/opencode --version" not in installer
    assert '"$temporary_dir/opencode" --version' in installer


def test_manifest_exports_connection_resources() -> None:
    manifest = _render_manifest()
    assert manifest["name"] == "opencode_server"
    resources = manifest["resources"]

    for collection in resources.values():
        for resource in collection.values():
            assert resource["project_id"] == PROJECT_ID

    node = resources["$core.compute.nodes"]["opencode_server"]
    assert node["cores"] == 2
    assert node["ram"] == 4096
    assert node["disk_spec"]["disks"][1] == {"label": "data", "size": 20}

    secret = resources["$core.secret.passwords"]["opencode_server_password"]
    assert secret["method"] == "AUTO_URL_SAFE"
    assert secret["constructor"] == {"kind": "plain"}
    assert secret["default_length"] == 48
    assert "value" not in secret

    values = resources["$core.vs.values"]
    assert values["opencode_server_endpoint"]["read_only"] is True
    assert values["opencode_server_endpoint"]["value"] == (
        'f"http://{$core.compute.nodes.$opencode_server:default_network:ipv4}:4096"'
    )
    assert values["opencode_server_login"]["value"] == "opencode"

    assert manifest["exports"] == {
        "endpoint": {
            "kind": "resource",
            "link": "$core.vs.variables.$opencode_server_endpoint",
        },
        "login": {
            "kind": "resource",
            "link": "$core.vs.variables.$opencode_server_login",
        },
        "password": {
            "kind": "resource",
            "link": "$core.secret.passwords.$opencode_server_password",
        },
    }


def test_secret_is_injected_into_a_protected_environment_file() -> None:
    resources = _render_manifest()["resources"]
    config = resources["$core.config.configs"]["opencode_server_environment"]
    assert config["path"] == "/etc/opencode_server/server.env"
    assert config["mode"] == "0640"
    assert config["owner"] == "root"
    assert config["group"] == "opencode"
    assert config["body"]["kind"] == "text"
    content = config["body"]["content"]
    assert content.startswith('f"')
    assert "$core.secret.passwords.$opencode_server_password:value" in content
    assert "OPENCODE_SERVER_USERNAME=" in content
    assert "OPENCODE_SERVER_PASSWORD=" in content


def test_service_is_network_accessible_authenticated_and_persistent() -> None:
    unit = (ROOT / "etc/systemd/opencode-server.service").read_text()
    assert "User=opencode\n" in unit
    assert "Group=opencode\n" in unit
    assert "EnvironmentFile=/etc/opencode_server/server.env" in unit
    assert "ExecStartPre=/usr/local/bin/opencode-server-validate" in unit
    assert "--hostname 0.0.0.0 --port 4096" in unit
    assert "WorkingDirectory=/var/lib/opencode_server/workspace" in unit
    assert "ReadWritePaths=/var/lib/opencode_server" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit

    installer = (ROOT / "exordos/images/install.sh").read_text()
    assert 'SERVICE_USER="opencode"' in installer
    assert 'SERVICE_GROUP="opencode"' in installer
    assert "groupadd --system" in installer

    bootstrap = (ROOT / "exordos/images/bootstrap.sh").read_text()
    assert 'SERVICE_USER="opencode"' in bootstrap
    assert 'SERVICE_GROUP="opencode"' in bootstrap

    health = (ROOT / "scripts/opencode-server-health").read_text()
    assert "OPENCODE_SERVER_USERNAME" in health
    assert "OPENCODE_SERVER_PASSWORD" in health
    assert "http://127.0.0.1:4096/global/health" in health
    assert "--config -" in health

    assert bootstrap.index("source /usr/local/lib/exordos/lib_bootstrap.sh") < (
        bootstrap.index("set +x")
    )
    assert "find_persistent_disk" in bootstrap
    assert "migrate_to_persistent" in bootstrap
    assert 'mountpoint --quiet "$STATE_DIR"' in bootstrap
    assert "opencode-server-bootstrap-v1-complete" in bootstrap
    assert "OPENCODE_SERVER_USERNAME=opencode" in bootstrap
    assert "opencode-server-health" in bootstrap
    assert bootstrap.index("opencode-server-health") < bootstrap.rindex(
        '"$BOOTSTRAP_COMPLETE"'
    )


def test_runtime_configuration_disables_automatic_mutation() -> None:
    config = json.loads((ROOT / "etc/opencode.jsonc").read_text())
    assert config["autoupdate"] is False
    assert config["share"] == "disabled"


def test_element_workflow_builds_and_publishes_immutable_releases() -> None:
    workflow = (ROOT / ".github/workflows/exordos-element.yml").read_text()
    tests_workflow = (ROOT / ".github/workflows/tests.yaml").read_text()
    publish = workflow.split("- name: Publish element", 1)[1]

    assert workflow.count('"${EXORDOS_BIN}" build .') == 1
    assert workflow.count('"${EXORDOS_BIN}" push .') == 1
    assert (
        workflow.count("github.event_name == 'push' && github.ref_type == 'tag'") == 1
    )
    assert workflow.count("if: ${{ github.event_name == 'push' }}") == 2
    assert "actions: read" in workflow
    assert 'workflow_id: "tests.yaml"' in workflow
    assert "head_sha: context.sha" in workflow
    assert 'run => run.event === "push"' in workflow
    assert 'run => run.conclusion === "success"' in workflow
    assert "Timed out waiting for tests" in workflow
    assert 'GITHUB_REF_NAME}" =~ ^[0-9]+\\.[0-9]+\\.[0-9]+$' in workflow
    assert "opencode_server.raw.zst" in workflow
    assert "zstd --test" in workflow
    assert "EXORDOS_RELEASE_SHA256" in workflow
    assert "PUSH_CFG" in workflow
    assert "umask 077" in workflow
    assert "--force" not in publish
    assert "latest_arg=()" in publish
    assert 'if [[ "${GITHUB_REF_TYPE}" == "tag" ]]' in publish
    assert "latest_arg=(--latest)" in publish
    assert '"${latest_arg[@]}"' in publish
    assert 'branches: ["**"]' in workflow
    assert 'branches: ["**"]' in tests_workflow
    assert 'tags: ["*"]' in tests_workflow


def test_element_workflow_uses_the_internal_vm_runner() -> None:
    workflow = (ROOT / ".github/workflows/exordos-element.yml").read_text()

    assert "runs-on: [self-hosted, vm]" in workflow
    assert "command -v packer" in workflow
    assert "setup-packer" not in workflow
    assert "test -r /dev/kvm" in workflow
    assert "test -w /dev/kvm" in workflow
    assert "apt-get" not in workflow
    assert (
        "github.event.pull_request.head.repo.full_name != github.repository" in workflow
    )


def test_repository_contains_no_runtime_secret_or_internal_address() -> None:
    forbidden = (
        "OPENCODE_SERVER_PASSWORD=" + "opencode",
        ".".join(("192", "168", "")),
        ".".join(("10", "20", "")),
    )
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or ".venv" in path.parts:
            continue
        relative_parts = path.relative_to(ROOT).parts
        if relative_parts[0] in {"build", "dist", "output"}:
            continue
        if any(part.startswith(".") for part in relative_parts):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        assert not any(value in content for value in forbidden), path
