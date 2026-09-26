# GCP deployment with Terraform (free tier)

Provisions an always-free `e2-micro` VM in `us-central1`, opens port 8000,
clones this repo, writes `.env` from Terraform variables, and runs the bot
as a systemd service (starts on boot, restarts on failure).

Stays at $0/month as long as you keep the free-tier guardrails below.

## Prerequisites

1. A GCP project with **billing enabled** (card on file; stays $0 inside limits).
2. [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.5.
3. [gcloud CLI](https://cloud.google.com/sdk/docs/install), authenticated:
   ```bash
   gcloud auth application-default login
   gcloud config set project YOUR_PROJECT_ID
   ```

## Terraform Cloud

The config uses HCP Terraform (organization `ops-trade-idea`, workspace
`ops-trade-idea-gcp`) for remote state and runs. On your Mac:

```bash
terraform login   # once; opens a browser to approve the token
```

Then set variables in the workspace UI (**Variables** tab), marking secrets
sensitive:

- `project_id`, plus every `TF_VAR_*` secret from `terraform.tfvars.example`
  (`optionomics_api_key`, `webull_app_key`, `webull_app_secret`, `ios_api_key`, …)
- `GOOGLE_CREDENTIALS` — contents of a GCP service-account key JSON, so
  remote runs can authenticate to your project

After that, `terraform plan` / `terraform apply` run remotely from the
workspace. To go back to local state, remove the `cloud {}` block from
`main.tf` and re-run `terraform init`.

## Deploy

```bash
cd terraform/gcp
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: set project_id and your secrets
terraform init
terraform plan
terraform apply
```

Secrets can also be passed as env vars instead of `terraform.tfvars`:

```bash
TF_VAR_ios_api_key="..." TF_VAR_webull_app_key="..." terraform apply
```

After apply, Terraform prints the dashboard URL (`http://<ip>:8000`) and an
SSH command. The startup script takes a few minutes on first boot; check
progress with:

```bash
gcloud compute ssh ops-paper-trade --zone us-central1-a --project YOUR_PROJECT_ID
sudo journalctl -u ops-paper-trade -f
```

## Free-tier guardrails (stay at $0)

- Machine type stays `e2-micro`, zone stays in `us-central1` — changing
  either bills you.
- Boot disk stays 10 GB `pd-standard` (30 GB-mo free).
- Uses the auto-assigned ephemeral IP (720 free hrs/mo per account).
  Do NOT reserve a static IP without attaching it (~$7/mo).
- Outbound data stays under 1 GB/mo (bot polling + dashboard is far below).
- Set a $1 budget alert in Cloud Billing after first deploy.

## Notes

- `DRY_RUN` defaults to `true`. The bot only submits paper orders.
- Terraform state (`terraform.tfstate`) contains your secrets in plaintext —
  keep it local and never commit it (already gitignored).
- `terraform.tfvars` is gitignored. Only `terraform.tfvars.example` (no
  secrets) is committed.
- To tear down: `terraform destroy`.

## Files

- `main.tf` — provider, VM, firewall rule for port 8000.
- `variables.tf` — all bot settings; secrets marked `sensitive`.
- `startup.sh.tftpl` — first-boot script: installs uv, clones the repo,
  writes `.env`, enables the systemd service.
- `outputs.tf` — public IP, dashboard URL, SSH command.
