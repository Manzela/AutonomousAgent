variable "project_id" {
  description = "GCP project ID hosting Model Armor + SDP resources for the J1 trajectory shipper."
  type        = string
  default     = "i-for-ai"
}

variable "region" {
  description = "GCP region for the regional google_model_armor_template (Model Armor is regional; us-central1 is the canonical home for J1)."
  type        = string
  default     = "us-central1"
}

variable "inspect_template_display_name" {
  description = "Display name for the DLP/SDP InspectTemplate that drives Model Armor redaction."
  type        = string
  default     = "j1-inspect-and-redact"
}

variable "info_types" {
  description = "DLP InfoTypes inspected on each Model Armor pass. Baseline covers the highest-bleed PII categories for RLAIF training substrate. Expand as additional risk surfaces are identified."
  type        = list(string)
  default = [
    "EMAIL_ADDRESS",
    "CREDIT_CARD_NUMBER",
    "PHONE_NUMBER",
    "US_SOCIAL_SECURITY_NUMBER",
  ]
}

variable "min_likelihood" {
  description = "Minimum likelihood threshold for InfoType matches. LIKELIHOOD_LOW errs aggressively toward redaction; acceptable here because J1 trajectories feed offline training (over-redaction is harmless, leakage is a compliance time-bomb)."
  type        = string
  default     = "LIKELIHOOD_LOW"
}
