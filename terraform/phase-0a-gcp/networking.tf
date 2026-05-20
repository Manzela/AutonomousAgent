# Phase 0a — VPC + subnet for the AutonomousAgent GCE VM.
#
# Naming: `autonomousagent-*` prefix to avoid collision with sibling
# workloads on the shared i-for-ai project (the only existing network is
# `default`, which we leave untouched).
#
# CIDR: 10.10.0.0/24 is RFC-1918 private space; deliberately disjoint
# from GCP's default auto-mode subnets (10.128.0.0/9) so dual-VPC
# routing remains predictable if any sibling workload joins later.
#
# private_ip_google_access = true: the VM has no public IP (egress via
# Cloud NAT or direct google API routes), so this flag is required for
# pulling images from Artifact Registry, fetching secrets, and emitting
# logs without leaving the GCP backbone.

resource "google_compute_network" "autonomousagent" {
  name                            = "autonomousagent-vpc"
  auto_create_subnetworks         = false
  routing_mode                    = "REGIONAL"
  delete_default_routes_on_create = false
  depends_on                      = [google_project_service.enabled]
}

resource "google_compute_subnetwork" "autonomousagent" {
  name                     = "autonomousagent-subnet-us-central1"
  ip_cidr_range            = "10.10.0.0/24"
  region                   = var.region
  network                  = google_compute_network.autonomousagent.id
  private_ip_google_access = true
}

# Firewall — three rules implementing default-deny + IAP-only SSH + open egress.
#
# Rule ordering (GCP applies lowest-priority-number first):
#   1000  allow_iap_ssh        — SSH from GCP-published IAP CIDR only
#   1000  allow_egress_all     — VM can reach internet (Artifact Registry,
#                                Secret Manager, Cloud Logging, etc.)
#   65534 deny_all_ingress    — catch-all; blocks every other inbound packet
#
# target_tags = ["autonomousagent-vm"]: rules only apply to instances
# tagged this way (the GCE VM in compute.tf will carry this tag), so the
# rules cannot accidentally bleed onto sibling workloads on this VPC.

resource "google_compute_firewall" "deny_all_ingress" {
  name      = "autonomousagent-deny-all-ingress"
  network   = google_compute_network.autonomousagent.name
  direction = "INGRESS"
  priority  = 65534

  deny { protocol = "all" }
  source_ranges = ["0.0.0.0/0"]
}

resource "google_compute_firewall" "allow_iap_ssh" {
  name      = "autonomousagent-allow-iap-ssh"
  network   = google_compute_network.autonomousagent.name
  direction = "INGRESS"
  priority  = 1000

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
  # GCP-published IAP CIDR — fixed, do not parameterize.
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["autonomousagent-vm"]
}

resource "google_compute_firewall" "allow_egress_all" {
  name      = "autonomousagent-allow-egress-all"
  network   = google_compute_network.autonomousagent.name
  direction = "EGRESS"
  priority  = 1000

  allow { protocol = "all" }
  destination_ranges = ["0.0.0.0/0"]
  target_tags        = ["autonomousagent-vm"]
}

# Cloud Router + Cloud NAT: provides outbound internet access for the VM
# without a public IP. Required for apt-get (Debian mirrors), Docker
# installation (download.docker.com), and any non-GCP egress.
#
# AUTO_ONLY: GCP auto-allocates ephemeral external IPs for NAT — no
# static IP reservation needed. ERRORS_ONLY log filter keeps log volume
# manageable while still capturing NAT failures.

resource "google_compute_router" "autonomousagent" {
  project = var.project_id
  name    = "autonomousagent-router"
  region  = var.region
  network = google_compute_network.autonomousagent.id
}

resource "google_compute_router_nat" "autonomousagent" {
  project                            = var.project_id
  name                               = "autonomousagent-nat"
  router                             = google_compute_router.autonomousagent.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}
