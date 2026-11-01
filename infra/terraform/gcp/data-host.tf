# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

resource "google_service_account" "data_host" {
  account_id   = "${var.name_prefix}-data"
  display_name = "Observal data host"
}

resource "google_project_iam_member" "data_host_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.data_host.email}"
}

resource "google_project_iam_member" "data_host_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.data_host.email}"
}

resource "google_storage_bucket_iam_member" "data_host_backups" {
  bucket = google_storage_bucket.backups.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.data_host.email}"
}

resource "google_compute_disk" "data" {
  name = "${local.name}-data-disk"
  type = "pd-ssd"
  size = var.data_disk_size_gb
  zone = "${var.region}-a"
}

resource "google_compute_instance" "data_host" {
  name         = "${local.name}-data"
  machine_type = var.data_machine_type
  zone         = "${var.region}-a"
  tags         = ["data-host"]

  boot_disk {
    initialize_params {
      image = "projects/cos-cloud/global/images/family/cos-stable"
      size  = 30
      type  = "pd-balanced"
    }
  }

  attached_disk {
    source      = google_compute_disk.data.self_link
    device_name = "data-disk"
  }

  network_interface {
    subnetwork = google_compute_subnetwork.main.self_link
  }

  service_account {
    email  = google_service_account.data_host.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  metadata_startup_script = templatefile("${path.module}/user-data.sh.tftpl", {
    clickhouse_password              = random_password.clickhouse.result
    clickhouse_db                    = "observal"
    data_retention_days              = var.data_retention_days
    backups_bucket                   = google_storage_bucket.backups.name
    grafana_admin_user               = "admin"
    grafana_root_url                 = local.enable_custom_domain ? "https://${var.domain_name}" : ""
    observability_prometheus_enabled = local.observability_prometheus_enabled
    observability_grafana_enabled    = local.observability_grafana_enabled
  })

  allow_stopping_for_update = true
}
