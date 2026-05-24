output "floor_setting_id" {
  description = "Fully-qualified resource id of the project-level Model Armor FloorSetting."
  value       = google_model_armor_floorsetting.project.id
}

output "inspect_template_id" {
  description = "Fully-qualified DLP InspectTemplate id referenced by Model Armor."
  value       = google_data_loss_prevention_inspect_template.j1.id
}

output "model_armor_template_id" {
  description = "Resource id of the j1-trajectory-shipper Model Armor template; the J1 shipper code calls templates.sanitize against this id."
  value       = google_model_armor_template.j1_trajectory_shipper.id
}

output "model_armor_template_name" {
  description = "Short name of the Model Armor template (for application-layer config)."
  value       = google_model_armor_template.j1_trajectory_shipper.template_id
}
