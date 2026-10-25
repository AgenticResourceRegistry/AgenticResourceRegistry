# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.work@gmail.com>
# SPDX-License-Identifier: Apache-2.0

resource "google_service_account" "cloud_run" {
  account_id   = "${var.name_prefix}-run"
  display_name = "Observal Cloud Run service account"

  depends_on = [google_project_service.iam]
}

resource "google_project_iam_member" "cloud_run_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_project_iam_member" "cloud_run_trace_writer" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}

# ── API service ───────────────────────────────────────────────────────────

resource "google_cloud_run_v2_service" "api" {
  name     = "${local.name}-api"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  depends_on = [google_project_service.run]

  template {
    service_account = google_service_account.cloud_run.email

    vpc_access {
      connector = google_vpc_access_connector.main.id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    scaling {
      min_instance_count = var.api_min_instances
      max_instance_count = var.api_max_instances
    }

    containers {
      image = local.image_api
      args  = ["/app/.venv/bin/python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = var.api_cpu
          memory = var.api_memory
        }
      }

      env {
        name  = "SKIP_DDL_ON_STARTUP"
        value = "true"
      }

      env {
        name  = "JWT_KEY_DIR"
        value = "/tmp/keys"
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app["DATABASE_URL"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "REDIS_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app["REDIS_URL"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app["SECRET_KEY"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "CLICKHOUSE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app["CLICKHOUSE_URL"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "GOOGLE_OAUTH_CLIENT_ID"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app["GOOGLE_OAUTH_CLIENT_ID"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "GOOGLE_OAUTH_CLIENT_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app["GOOGLE_OAUTH_CLIENT_SECRET"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "GOOGLE_OAUTH_ALLOWED_DOMAINS"
        value = var.google_oauth_allowed_domains
      }

      startup_probe {
        http_get {
          path = "/readyz"
          port = 8000
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 10
      }

      liveness_probe {
        http_get {
          path = "/readyz"
          port = 8000
        }
        period_seconds    = 15
        failure_threshold = 3
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "api_public" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ── Web service ───────────────────────────────────────────────────────────

resource "google_cloud_run_v2_service" "web" {
  name     = "${local.name}-web"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.cloud_run.email

    scaling {
      min_instance_count = var.web_min_instances
      max_instance_count = var.web_max_instances
    }

    containers {
      image = local.image_web

      ports {
        container_port = 3000
      }

      resources {
        limits = {
          cpu    = var.web_cpu
          memory = var.web_memory
        }
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "web_public" {
  name     = google_cloud_run_v2_service.web.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ── Worker service ────────────────────────────────────────────────────────

resource "google_cloud_run_v2_service" "worker" {
  name     = "${local.name}-worker"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.cloud_run.email

    annotations = {
      "run.googleapis.com/cpu-throttling" = "false"
    }

    vpc_access {
      connector = google_vpc_access_connector.main.id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    scaling {
      min_instance_count = var.worker_min_instances
      max_instance_count = var.worker_max_instances
    }

    containers {
      image = local.image_api
      args  = ["/app/.venv/bin/python", "-c", "import asyncio; asyncio.set_event_loop(asyncio.new_event_loop()); from arq import run_worker; from worker import WorkerSettings; run_worker(WorkerSettings)"]

      resources {
        limits = {
          cpu    = var.worker_cpu
          memory = var.worker_memory
        }
      }

      env {
        name  = "JWT_KEY_DIR"
        value = "/tmp/keys"
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app["DATABASE_URL"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "REDIS_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app["REDIS_URL"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app["SECRET_KEY"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "CLICKHOUSE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app["CLICKHOUSE_URL"].secret_id
            version = "latest"
          }
        }
      }
    }
  }
}

# ── Init job (migrations) ─────────────────────────────────────────────────

resource "google_cloud_run_v2_job" "init" {
  name     = "${local.name}-init"
  location = var.region

  template {
    template {
      service_account = google_service_account.cloud_run.email

      vpc_access {
        connector = google_vpc_access_connector.main.id
        egress    = "PRIVATE_RANGES_ONLY"
      }

      max_retries = 1
      timeout     = "300s"

      containers {
        image   = local.image_api
        command = ["/app/entrypoint.sh"]

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        env {
          name  = "JWT_KEY_DIR"
          value = "/tmp/keys"
        }

        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app["DATABASE_URL"].secret_id
              version = "latest"
            }
          }
        }

        env {
          name = "REDIS_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app["REDIS_URL"].secret_id
              version = "latest"
            }
          }
        }

        env {
          name = "SECRET_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app["SECRET_KEY"].secret_id
              version = "latest"
            }
          }
        }

        env {
          name = "CLICKHOUSE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app["CLICKHOUSE_URL"].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }
}
