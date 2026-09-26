output "instance_name" {
  description = "Name of the Compute Engine instance."
  value       = google_compute_instance.bot.name
}

output "external_ip" {
  description = "Public IP of the bot VM."
  value       = google_compute_instance.bot.network_interface[0].access_config[0].nat_ip
}

output "dashboard_url" {
  description = "URL of the bot dashboard."
  value       = "http://${google_compute_instance.bot.network_interface[0].access_config[0].nat_ip}:8000"
}

output "ssh_command" {
  description = "Command to SSH into the VM."
  value       = "gcloud compute ssh ${google_compute_instance.bot.name} --zone ${var.zone} --project ${var.project_id}"
}
