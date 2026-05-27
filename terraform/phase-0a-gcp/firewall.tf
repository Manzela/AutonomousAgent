# Egress firewall rules — defence-in-depth allowlist (P2-27).
#
# Replaces the broad allow_egress_all (all protocols, 0.0.0.0/0) that was
# previously in networking.tf. A compromised container could previously reach
# any internet host on any port. This allowlist restricts outbound to known
# required protocols and ports.
#
# Design:
#   Priority 1000  allow rules   — matched first; specific protocols allowed
#   Priority 65534 deny_egress_all — overrides GCP's implied allow-all at 65535;
#                                    blocks anything not matched above
#
# Services and the port they need:
#   HTTPS (443) : Vertex AI, Anthropic, GitHub, Telegram, Honcho, Chroma Cloud,
#                 Artifact Registry, GCS, Secret Manager, Cloud Logging (via
#                 Private Google Access VIPs 199.36.153.4/30 + .8/30, also 443)
#   HTTP (80)   : apt-get (Debian mirrors), Docker image layer pulls
#   DNS (53)    : UDP + TCP to resolve external hostnames via Cloud NAT
#   TCP 3307    : Cloud SQL Auth Proxy v2 connector protocol (to Cloud SQL
#                 managed endpoints — goes via Google backbone, still needs egress)
#   TCP 6379    : Cloud Memorystore Redis (private VPC IP, same network)
#
# All rules are scoped to target_tags = ["autonomousagent-vm"] so they cannot
# bleed onto other GCE instances that may share this VPC in the future.

resource "google_compute_firewall" "allow_egress_https" {
  name      = "autonomousagent-allow-egress-https"
  network   = google_compute_network.autonomousagent.name
  direction = "EGRESS"
  priority  = 1000

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }
  # 0.0.0.0/0 is intentional — the destination APIs (Vertex AI, GitHub,
  # Telegram, Anthropic, Honcho, Chroma Cloud) do not have stable IP ranges.
  # Domain-based restriction requires Cloud Armor (L7), not VPC firewall (L3/4).
  destination_ranges = ["0.0.0.0/0"]
  target_tags        = ["autonomousagent-vm"]
}

resource "google_compute_firewall" "allow_egress_http" {
  name      = "autonomousagent-allow-egress-http"
  network   = google_compute_network.autonomousagent.name
  direction = "EGRESS"
  priority  = 1000

  allow {
    protocol = "tcp"
    ports    = ["80"]
  }
  destination_ranges = ["0.0.0.0/0"]
  target_tags        = ["autonomousagent-vm"]
}

resource "google_compute_firewall" "allow_egress_dns" {
  name      = "autonomousagent-allow-egress-dns"
  network   = google_compute_network.autonomousagent.name
  direction = "EGRESS"
  priority  = 1000

  allow {
    protocol = "udp"
    ports    = ["53"]
  }
  destination_ranges = ["0.0.0.0/0"]
  target_tags        = ["autonomousagent-vm"]
}

resource "google_compute_firewall" "allow_egress_dns_tcp" {
  name      = "autonomousagent-allow-egress-dns-tcp"
  network   = google_compute_network.autonomousagent.name
  direction = "EGRESS"
  priority  = 1000

  allow {
    protocol = "tcp"
    ports    = ["53"]
  }
  destination_ranges = ["0.0.0.0/0"]
  target_tags        = ["autonomousagent-vm"]
}

resource "google_compute_firewall" "allow_egress_cloudsql" {
  name      = "autonomousagent-allow-egress-cloudsql"
  network   = google_compute_network.autonomousagent.name
  direction = "EGRESS"
  priority  = 1000

  allow {
    protocol = "tcp"
    ports    = ["3307"]
  }
  # Cloud SQL connector goes to Google-managed IPs; restrict to Google's
  # global IP range where Cloud SQL endpoints are published.
  # 34.0.0.0/8 covers the primary GCP IP ranges used for Cloud SQL.
  destination_ranges = ["34.0.0.0/8", "35.0.0.0/8"]
  target_tags        = ["autonomousagent-vm"]
}

resource "google_compute_firewall" "allow_egress_redis" {
  name      = "autonomousagent-allow-egress-redis"
  network   = google_compute_network.autonomousagent.name
  direction = "EGRESS"
  priority  = 1000

  allow {
    protocol = "tcp"
    ports    = ["6379"]
  }
  # Memorystore is on the private VPC subnet — restrict to the subnet CIDR
  # rather than allowing Redis port to the entire internet.
  destination_ranges = [google_compute_subnetwork.autonomousagent.ip_cidr_range]
  target_tags        = ["autonomousagent-vm"]
}

# Default-deny all remaining egress.
# This overrides GCP's implied allow-all-egress at priority 65535.
# Any outbound traffic not matched by the 1000-priority allow rules above is
# dropped here. A compromised container that tries to beacon on arbitrary
# ports (e.g., port 4444 C2) hits this deny.
resource "google_compute_firewall" "deny_egress_all" {
  name      = "autonomousagent-deny-egress-all"
  network   = google_compute_network.autonomousagent.name
  direction = "EGRESS"
  priority  = 65534

  deny { protocol = "all" }
  destination_ranges = ["0.0.0.0/0"]
  target_tags        = ["autonomousagent-vm"]
}
