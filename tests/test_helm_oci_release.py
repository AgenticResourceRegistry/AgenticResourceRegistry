# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
HELM_OCI_REF = "oci://ghcr.io/observal/charts/observal"


def _load_yaml(path: str):
    return yaml.safe_load((ROOT / path).read_text())


def test_release_workflow_publishes_helm_chart_to_ghcr_oci():
    workflow = _load_yaml(".github/workflows/release.yml")
    job = workflow["jobs"]["helm-chart"]

    assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert "approve" in job["needs"]

    step_runs = "\n".join(step.get("run", "") for step in job["steps"])

    assert 'HELM_OCI_REPO="oci://ghcr.io/observal/charts"' in step_runs
    assert 'HELM_OCI_REF="ghcr.io/observal/charts/observal"' in step_runs
    assert "helm registry login ghcr.io" in step_runs
    assert "oras login ghcr.io" in step_runs
    assert 'helm push "chart-dist/observal-${VERSION}.tgz" "$HELM_OCI_REPO"' in step_runs
    assert '"${HELM_OCI_REF}:artifacthub.io"' in step_runs
    assert "application/vnd.cncf.artifacthub.repository-metadata.layer.v1.yaml" in step_runs


def test_release_workflow_does_not_publish_helm_chart_to_github_pages():
    workflow_text = (ROOT / ".github/workflows/release.yml").read_text()
    helm_job_text = workflow_text.split("  helm-chart:", maxsplit=1)[1].split("  release:", maxsplit=1)[0]

    assert "gh-pages" not in helm_job_text
    assert "helm repo index" not in helm_job_text
    assert "observal.github.io" not in helm_job_text


def test_chart_has_artifacthub_metadata_for_oci_listing():
    chart = _load_yaml("infra/helm/observal/Chart.yaml")

    assert chart["icon"] == "https://raw.githubusercontent.com/Observal/Observal/main/docs/logo.svg"
    assert chart["maintainers"][0]["email"] == "harisrini21@gmail.com"
    assert chart["annotations"]["artifacthub.io/category"] == "monitoring-logging"
    assert chart["annotations"]["artifacthub.io/license"] == "Apache-2.0"
    assert "Documentation" in chart["annotations"]["artifacthub.io/links"]


def test_artifacthub_repo_metadata_has_registerd_verified_publisher_id():
    metadata_text = (ROOT / "infra/helm/artifacthub-repo.yml").read_text()
    metadata = yaml.safe_load(metadata_text)

    # assert metadata["owners"] == [{"name": "Observal", "email": "harisrini21@gmail.com"}]
    # assert "repositoryID:" in metadata_text
    # assert "00000000-0000-0000-0000-000000000000" in metadata_text
    assert metadata["owners"] == [{"name": "Observal", "email": "harisrini21@gmail.com"}]
    assert metadata["repositoryID"] == "bcccc095-d7e6-4ae3-b40c-b5c2b6fb2f80"


def test_app_workload_image_tags_default_to_chart_app_version():
    values = _load_yaml("infra/helm/observal/values.yaml")
    helpers_text = (ROOT / "infra/helm/observal/templates/_helpers.tpl").read_text()

    assert values["api"]["image"]["tag"] == ""
    assert values["worker"]["image"]["tag"] == ""
    assert values["web"]["image"]["tag"] == ""
    assert '$tag := .image.tag | default (.appVersion | default "latest")' in helpers_text

    for template_path in (
        "infra/helm/observal/templates/api-deployment.yaml",
        "infra/helm/observal/templates/init-job.yaml",
        "infra/helm/observal/templates/worker-deployment.yaml",
        "infra/helm/observal/templates/web-deployment.yaml",
    ):
        assert '"appVersion" .Chart.AppVersion' in (ROOT / template_path).read_text()


def test_ci_asserts_packaged_chart_uses_app_version_image_tags():
    workflow_text = (ROOT / ".github/workflows/ci.yml").read_text()

    assert "helm package infra/helm/observal" in workflow_text
    assert '--app-version "$VERSION"' in workflow_text
    assert 'helm template observal "chart-dist/observal-${VERSION}.tgz"' in workflow_text
    assert 'API_IMAGE_COUNT="$(grep -c "image: ghcr.io/observal/observal-api:${VERSION}"' in workflow_text
    assert 'test "$API_IMAGE_COUNT" -ge 3' in workflow_text
    assert 'grep -q "image: ghcr.io/observal/observal-web:${VERSION}"' in workflow_text
    assert "--set api.image.tag=custom-api" in workflow_text
    assert "--set worker.image.tag=custom-worker" in workflow_text
    assert "--set web.image.tag=custom-web" in workflow_text
    assert 'grep -q "image: ghcr.io/observal/observal-api:custom-api"' in workflow_text
    assert 'grep -q "image: ghcr.io/observal/observal-api:custom-worker"' in workflow_text
    assert 'grep -q "image: ghcr.io/observal/observal-web:custom-web"' in workflow_text


def test_helm_docs_use_oci_install_instead_of_repo_add():
    docs_text = (ROOT / "docs/self-hosting/kubernetes-helm.md").read_text()
    chart_readme_text = (ROOT / "infra/helm/observal/README.md").read_text()

    assert HELM_OCI_REF in docs_text
    assert HELM_OCI_REF in chart_readme_text
    assert "helm repo add observal" not in docs_text
    assert "observal.github.io" not in docs_text
    assert "helm repo add observal" not in chart_readme_text
    assert "observal.github.io" not in chart_readme_text
