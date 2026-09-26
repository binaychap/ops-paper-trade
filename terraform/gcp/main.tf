terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

# Free-tier VM: e2-micro in us-central1 stays $0 (always free, no expiry).
# Do NOT change machine_type/zone/disk type or you leave the free tier.
resource "google_compute_instance" "bot" {
  name         = var.instance_name
  machine_type = "e2-micro"
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 10
      type  = "pd-standard" # free tier: 30 GB-mo standard disk
    }
  }

  network_interface {
    network = "default"
    access_config {} # ephemeral public IP (720 free hrs/mo per account)
  }

  metadata_startup_script = templatefile("${path.module}/startup.sh.tftpl", {
    repo_url                          = var.repo_url
    dry_run                           = var.dry_run
    max_notional_usd                  = var.max_notional_usd
    allow_short_selling               = var.allow_short_selling
    force_reprocess                   = var.force_reprocess
    optionomics_api_key               = var.optionomics_api_key
    optionomics_email                 = var.optionomics_email
    optionomics_api_url               = var.optionomics_api_url
    optionomics_poll_enabled          = var.optionomics_poll_enabled
    optionomics_poll_interval_seconds = var.optionomics_poll_interval_seconds
    webull_app_key                    = var.webull_app_key
    webull_app_secret                 = var.webull_app_secret
    webull_endpoint                   = var.webull_endpoint
    database_path                     = var.database_path
    bullish_profit_percent            = var.bullish_profit_percent
    bullish_stop_loss_percent         = var.bullish_stop_loss_percent
    bearish_profit_percent            = var.bearish_profit_percent
    bearish_stop_loss_percent         = var.bearish_stop_loss_percent
    iron_condor_profit_percent        = var.iron_condor_profit_percent
    iron_condor_stop_loss_percent     = var.iron_condor_stop_loss_percent
    options_margin_account_number     = var.options_margin_account_number
    bullish_stock_account_number      = var.bullish_stock_account_number
    bearish_quote_max_age_seconds     = var.bearish_quote_max_age_seconds
    iron_condor_quote_max_age_seconds = var.iron_condor_quote_max_age_seconds
    ios_api_key                       = var.ios_api_key
    morning_sell_enabled              = var.morning_sell_enabled
    morning_sell_time                 = var.morning_sell_time
    morning_sell_timezone             = var.morning_sell_timezone
    morning_sell_poll_seconds         = var.morning_sell_poll_seconds
  })

  tags = ["ops-paper-trade"]
}

# Open port 8000 to the world (same as the OCI security-list rule).
# SSH (22) is already allowed by the default-allow-ssh rule.
resource "google_compute_firewall" "bot_8000" {
  name    = "${var.instance_name}-allow-8000"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["8000"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["ops-paper-trade"]
}
