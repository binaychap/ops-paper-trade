variable "project_id" {
  description = "GCP project ID (billing must be enabled on the project)."
  type        = string
}

variable "region" {
  description = "Region. Keep us-central1 for the always-free e2-micro."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Zone. Keep a us-central1 zone for the always-free e2-micro."
  type        = string
  default     = "us-central1-a"
}

variable "instance_name" {
  description = "Compute Engine instance name."
  type        = string
  default     = "ops-paper-trade"
}

variable "repo_url" {
  description = "Git URL of the bot repository cloned by the startup script."
  type        = string
  default     = "https://github.com/binaychap/ops-paper-trade.git"
}

# --- Bot settings (defaults mirror .env.example; DRY_RUN stays true) ---

variable "dry_run" {
  type    = string
  default = "true"
}

variable "max_notional_usd" {
  type    = string
  default = "250"
}

variable "allow_short_selling" {
  type    = string
  default = "false"
}

variable "force_reprocess" {
  type    = string
  default = "false"
}

variable "optionomics_api_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "optionomics_email" {
  type    = string
  default = ""
}

variable "optionomics_api_url" {
  type    = string
  default = "https://optionomics.ai/api/v1/trade_ideas"
}

variable "optionomics_poll_enabled" {
  type    = string
  default = "true"
}

variable "optionomics_poll_interval_seconds" {
  type    = string
  default = "600"
}

variable "webull_app_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "webull_app_secret" {
  type      = string
  default   = ""
  sensitive = true
}

variable "webull_endpoint" {
  type    = string
  default = "api.sandbox.webull.com"
}

variable "database_path" {
  type    = string
  default = "bot.sqlite3"
}

variable "bullish_profit_percent" {
  type    = string
  default = "10"
}

variable "bullish_stop_loss_percent" {
  type    = string
  default = "5"
}

variable "bearish_profit_percent" {
  type    = string
  default = "20"
}

variable "bearish_stop_loss_percent" {
  type    = string
  default = "10"
}

variable "iron_condor_profit_percent" {
  type    = string
  default = "10"
}

variable "iron_condor_stop_loss_percent" {
  type    = string
  default = "5"
}

variable "options_margin_account_number" {
  type      = string
  default   = ""
  sensitive = true
}

variable "bullish_stock_account_number" {
  type      = string
  default   = ""
  sensitive = true
}

variable "bearish_quote_max_age_seconds" {
  type    = string
  default = "60"
}

variable "iron_condor_quote_max_age_seconds" {
  type    = string
  default = "60"
}

variable "ios_api_key" {
  description = "Bearer <redacted> for the iOS trading API. Empty disables /api/trading/*."
  type        = string
  default     = ""
  sensitive   = true
}

variable "morning_sell_enabled" {
  type    = string
  default = "false"
}

variable "morning_sell_time" {
  type    = string
  default = "10:00"
}

variable "morning_sell_timezone" {
  type    = string
  default = "America/New_York"
}

variable "morning_sell_poll_seconds" {
  type    = string
  default = "60"
}
