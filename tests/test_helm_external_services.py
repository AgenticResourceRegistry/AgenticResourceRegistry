# SPDX-FileCopyrightText: 2026 SK Akram <skakram00zz@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1] / "infra" / "helm" / "observal"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _helm(*args):
    return subprocess.run(["helm", "template", "t", str(CHART), *args], capture_output=True, text=True, check=False)


def _render(*args):
    result = _helm(*args)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _kinds(docs, kind):
    return [d for d in docs if d["kind"] == kind]


def _env(docs, deployment):
    dep = next(d for d in _kinds(docs, "Deployment") if d["metadata"]["name"] == f"t-observal-{deployment}")
    container = dep["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e["valueFrom"]["secretKeyRef"] for e in container["env"] if "valueFrom" in e}


def _init_container_names(docs):
    dep = next(d for d in _kinds(docs, "Deployment") if d["metadata"]["name"] == "t-observal-api")
    return {c["name"] for c in dep["spec"]["template"]["spec"]["initContainers"]}


def _external_urls(docs):
    secret = next(d for d in _kinds(docs, "Secret") if d["metadata"]["name"] == "t-observal-external")
    return secret["stringData"]


def test_default_render_deploys_all_embedded_services():
    docs = _render()
    assert len(_kinds(docs, "StatefulSet")) == 3
    assert not [d for d in _kinds(docs, "Secret") if d["metadata"]["name"] == "t-observal-external"]
    env = _env(docs, "api")
    assert {ref["name"] for ref in env.values()} == {"t-observal-secret"}
    assert {"wait-for-postgres", "wait-for-clickhouse", "wait-for-redis"} <= _init_container_names(docs)


def test_all_external_host_mode_removes_embedded_workloads():
    docs = _render("-f", str(CHART / "ci" / "external-values.yaml"))
    assert _kinds(docs, "StatefulSet") == []
    assert not [d for d in _kinds(docs, "Service") if d["metadata"]["name"].endswith(("-db", "-clickhouse", "-redis"))]
    assert not [d for d in _kinds(docs, "ConfigMap") if "clickhouse" in d["metadata"]["name"]]
    assert not {"wait-for-postgres", "wait-for-clickhouse", "wait-for-redis"} & _init_container_names(docs)
    for target in ("api", "worker"):
        env = _env(docs, target)
        assert {ref["name"] for ref in env.values()} == {"t-observal-external"}
    init = next(iter(_kinds(docs, "Job")))
    init_env = init["spec"]["template"]["spec"]["containers"][0]["env"]
    assert {e["name"] for e in init_env} == {"DATABASE_URL", "CLICKHOUSE_URL", "REDIS_URL"}


def test_external_urls_are_built_and_percent_encoded():
    urls = _external_urls(_render("-f", str(CHART / "ci" / "external-values.yaml")))
    assert (
        urls["DATABASE_URL"]
        == "postgresql+asyncpg://observal:p%40ss%2Bw%2Ford@db.example.internal:5432/observal?ssl=require"
    )
    assert urls["CLICKHOUSE_URL"] == "clickhouses://default:ch%3Asecret@abc.clickhouse.cloud:8443/observal"
    assert urls["REDIS_URL"] == "rediss://:redis-token@cache.example.internal:6379/0"


def test_existing_secret_mode_references_user_secret_and_mixes_with_embedded():
    docs = _render("-f", str(CHART / "ci" / "external-existing-secret-values.yaml"))
    env = _env(docs, "api")
    assert env["DATABASE_URL"] == {"name": "observal-pg", "key": "connection-uri"}
    assert env["REDIS_URL"] == {"name": "observal-redis", "key": "uri"}
    assert env["CLICKHOUSE_URL"] == {"name": "t-observal-secret", "key": "CLICKHOUSE_URL"}
    assert [d["metadata"]["name"] for d in _kinds(docs, "StatefulSet")] == ["t-observal-clickhouse"]
    assert not [d for d in _kinds(docs, "Secret") if d["metadata"]["name"] == "t-observal-external"]
    shared = next(d for d in _kinds(docs, "Secret") if d["metadata"]["name"] == "t-observal-secret")["stringData"]
    assert "DATABASE_URL" not in shared
    assert "REDIS_URL" not in shared
    assert "POSTGRES_PASSWORD" not in shared
    assert "CLICKHOUSE_URL" in shared


def test_user_supplied_shared_secret_is_not_rendered_and_external_still_works():
    docs = _render("-f", str(CHART / "ci" / "external-values.yaml"), "--set", "secrets.existingSecret=mine")
    assert not [d for d in _kinds(docs, "Secret") if d["metadata"]["name"] == "t-observal-secret"]
    assert _external_urls(docs)["DATABASE_URL"].startswith("postgresql+asyncpg://")


def test_embedded_disabled_without_external_fails():
    result = _helm("--set", "postgresql.enabled=false")
    assert result.returncode != 0
    assert "requires externalPostgres.enabled=true" in result.stderr


def test_external_without_source_fails():
    result = _helm("--set", "externalRedis.enabled=true")
    assert result.returncode != 0
    assert "externalRedis.existingSecret or externalRedis.host" in result.stderr


def test_external_url_omits_ssl_and_credentials_when_unset():
    docs = _render(
        "--set",
        "externalPostgres.enabled=true",
        "--set",
        "externalPostgres.host=pg",
        "--set",
        "externalPostgres.username=",
    )
    assert _external_urls(docs)["DATABASE_URL"] == "postgresql+asyncpg://pg:5432/observal"
