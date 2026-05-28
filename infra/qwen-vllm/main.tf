# W1.J — Qwen3-Coder-30B-A3B-Instruct vLLM Serving Infrastructure
#
# Decision: D-2.a (model), D-8 (spot pricing approved)
# Instance: a2-highgpu-1g (1× A100 80GB)
# Cost: ~$400/mo spot vs ~$2,600/mo on-demand
# Promote: change provisioning_model = "STANDARD" if preemption > 2/day

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
  }
}

variable "project_id" {
  type    = string
  default = "autonomous-agent-2026"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type    = string
  default = "us-central1-a"
}

variable "provisioning_model" {
  type        = string
  default     = "SPOT"
  description = "SPOT or STANDARD. Start with SPOT; promote if preemption > 2/day."

  validation {
    condition     = contains(["SPOT", "STANDARD"], var.provisioning_model)
    error_message = "provisioning_model must be SPOT or STANDARD."
  }
}

variable "network_name" {
  type    = string
  default = "autonomousagent-vpc"
}

variable "subnet_name" {
  type    = string
  default = "autonomousagent-subnet-us-central1"
}

# Use the existing VPC
data "google_compute_network" "vpc" {
  name    = var.network_name
  project = var.project_id
}

data "google_compute_subnetwork" "subnet" {
  name    = var.subnet_name
  project = var.project_id
  region  = var.region
}

# Service account for the vLLM instance
resource "google_service_account" "qwen_vllm" {
  project      = var.project_id
  account_id   = "qwen-vllm"
  display_name = "Qwen vLLM Serving SA"
}

# Minimal IAM: only needs to pull model from HuggingFace (internet egress)
# and write metrics to Cloud Monitoring
resource "google_project_iam_member" "qwen_vllm_monitoring" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.qwen_vllm.email}"
}

resource "google_project_iam_member" "qwen_vllm_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.qwen_vllm.email}"
}

# Firewall: allow internal TCP 8000 from hermes VM only
resource "google_compute_firewall" "allow_vllm_internal" {
  name    = "allow-vllm-internal"
  project = var.project_id
  network = data.google_compute_network.vpc.self_link

  allow {
    protocol = "tcp"
    ports    = ["8000"]
  }

  source_tags = ["hermes-agent"]
  target_tags = ["qwen-vllm"]
  direction   = "INGRESS"
}

# Startup script that pulls the model and starts vLLM
locals {
  startup_script = <<-SCRIPT
    #!/bin/bash
    set -euo pipefail

    # Install NVIDIA drivers if not present
    if ! command -v nvidia-smi &>/dev/null; then
      echo "Installing NVIDIA drivers..."
      apt-get update -qq
      apt-get install -y -qq linux-headers-$(uname -r) nvidia-driver-550-server
    fi

    # Install Docker + NVIDIA Container Toolkit
    if ! command -v docker &>/dev/null; then
      curl -fsSL https://get.docker.com | sh
      distribution=$(. /etc/os-release; echo "$ID$VERSION_ID")
      curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
      curl -s -L "https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list" | \
        sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
        tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
      apt-get update -qq
      apt-get install -y -qq nvidia-container-toolkit
      nvidia-ctk runtime configure --runtime=docker
      systemctl restart docker
    fi

    # Pull and run vLLM
    docker run -d \
      --name qwen-vllm \
      --restart unless-stopped \
      --gpus all \
      --shm-size 16g \
      -p 8000:8000 \
      -e HUGGING_FACE_HUB_TOKEN="$${HF_TOKEN:-}" \
      vllm/vllm-openai:v0.8.5 \
        --served-model-name Qwen/Qwen3-Coder-30B-A3B-Instruct \
        --model Qwen/Qwen3-Coder-30B-A3B-Instruct \
        --enable-auto-tool-choice \
        --tool-call-parser qwen3_coder \
        --max-model-len 32768 \
        --gpu-memory-utilization 0.90 \
        --dtype bfloat16 \
        --enforce-eager \
        --trust-remote-code \
        --port 8000

    echo "vLLM started. Waiting for health..."
    for i in $(seq 1 120); do
      if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "vLLM healthy after $${i}s"
        exit 0
      fi
      sleep 5
    done
    echo "WARNING: vLLM did not become healthy within 600s"
  SCRIPT
}

resource "google_compute_instance" "qwen_vllm" {
  name         = "qwen-vllm"
  project      = var.project_id
  zone         = var.zone
  machine_type = "a2-highgpu-1g"

  tags = ["qwen-vllm"]

  scheduling {
    provisioning_model  = var.provisioning_model
    on_host_maintenance = "TERMINATE"
    automatic_restart   = var.provisioning_model == "STANDARD" ? true : false

    # Spot instances: preemptible behavior
    preemptible = var.provisioning_model == "SPOT" ? true : false
  }

  guest_accelerator {
    type  = "nvidia-a100-80gb"
    count = 1
  }

  boot_disk {
    initialize_params {
      image = "projects/ml-images/global/images/family/common-gpu-debian-12-py312"
      size  = 200 # GB — model weights are ~15GB; OS + Docker layers need room
      type  = "pd-balanced"
    }
  }

  network_interface {
    subnetwork = data.google_compute_subnetwork.subnet.self_link

    access_config {
      # Ephemeral public IP for HuggingFace model download.
      # Once model is cached, this can be removed.
    }
  }

  service_account {
    email  = google_service_account.qwen_vllm.email
    scopes = ["cloud-platform"]
  }

  metadata_startup_script = local.startup_script

  labels = {
    purpose     = "qwen-vllm-serving"
    cost-center = "w1j"
    tier        = "privacy"
  }

  lifecycle {
    # Prevent accidental deletion of a running model server
    prevent_destroy = false
  }
}

# Preemption alert — fires if preempted > 2 times/day (D-8 escalation trigger)
resource "google_monitoring_alert_policy" "qwen_preemption" {
  project      = var.project_id
  display_name = "Qwen vLLM Spot Preemption Rate"
  combiner     = "OR"

  conditions {
    display_name = "Preemption count > 2 in 24h"
    condition_threshold {
      filter          = "resource.type = \"gce_instance\" AND resource.labels.instance_id = \"${google_compute_instance.qwen_vllm.instance_id}\" AND metric.type = \"compute.googleapis.com/instance/preemptions\""
      comparison      = "COMPARISON_GT"
      threshold_value = 2
      duration        = "0s"

      aggregations {
        alignment_period   = "86400s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = []

  documentation {
    content   = "Qwen vLLM Spot instance preempted >2 times in 24h. Per D-8: promote to on-demand (change provisioning_model=STANDARD in infra/qwen-vllm/main.tf)."
    mime_type = "text/markdown"
  }
}

output "qwen_vllm_internal_ip" {
  description = "Internal IP for LiteLLM api_base configuration"
  value       = google_compute_instance.qwen_vllm.network_interface[0].network_ip
}

output "qwen_vllm_instance_name" {
  value = google_compute_instance.qwen_vllm.name
}
