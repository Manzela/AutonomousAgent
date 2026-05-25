#!/bin/bash
# Validation script for Model Armor configuration on project autonomous-agent-2026
# This script verifies that the floor settings and SDP templates are correctly configured.

PROJECT_ID="autonomous-agent-2026"
LOCATION="global"
TEMPLATE_ID="j1-trajectory-shipper"
REGIONAL_LOCATION="us-central1"

echo "Checking Model Armor API enablement..."
gcloud services list --project="${PROJECT_ID}" --filter="name:modelarmor.googleapis.com" --format="value(config.name)"

echo "Checking DLP API enablement..."
gcloud services list --project="${PROJECT_ID}" --filter="name:dlp.googleapis.com" --format="value(config.name)"

echo "Describing DLP Inspect Template..."
gcloud dlp templates describe "j1-inspect-and-redact" --project="${PROJECT_ID}"

echo "Describing Project Floor Settings..."
gcloud model-armor floorsettings describe --project="${PROJECT_ID}" --location="${LOCATION}"

echo "Describing Model Armor Template: ${TEMPLATE_ID}..."
gcloud model-armor templates describe "${TEMPLATE_ID}" --project="${PROJECT_ID}" --location="${REGIONAL_LOCATION}"

echo "Verification complete."
