# ADR-001: VPC Egress Restriction Reversion

## Status
Accepted

## Context
A prior change attempted to restrict HTTPS (port 443) egress from the AutonomousAgent VM to `10.0.0.10/32`, which was intended as a placeholder/target for a future L7 egress proxy (Squid/Envoy) to perform domain whitelisting (Vertex AI, Anthropic, GitHub, Telegram).

However, since this egress proxy has not yet been provisioned or configured, setting the destination range to `10.0.0.10/32` breaks all external API communications from the VM (causing timeouts for Vertex AI and other services).

## Decision
We revert the `destination_ranges` for egress port 443 in `terraform/phase-0a-gcp/firewall.tf` back to `["0.0.0.0/0"]`.

The domain whitelisting egress proxy is a separate workstream (tracked as ticket P3-10 / P3-11). Until that proxy is fully operational and configured in our network, broad egress is required.

## Consequences
- The AutonomousAgent VM will have direct egress to the public internet on port 443.
- All external API calls will function correctly without proxy routing.
- Egress restriction to specific domains is deferred to the future L7 proxy implementation.
