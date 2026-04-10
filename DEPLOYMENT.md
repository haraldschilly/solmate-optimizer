# Deployment on GCP (Cloud Run Jobs + Cloud Scheduler)

## Prerequisites

- `gcloud` CLI authenticated with a GCP project
- Artifact Registry Docker repository in your project
- Cloud Run and Cloud Scheduler APIs enabled

```bash
export GCP_PROJECT="your-project-id"
export GCP_REGION="europe-west3"
export GCP_REPO="your-artifact-registry-repo"

gcloud config set project $GCP_PROJECT
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com
```

## 1. Build and push the container

```bash
gcloud builds submit \
  --tag ${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${GCP_REPO}/solmate-optimizer
```

## 2. Create the Cloud Run job

```bash
gcloud run jobs create solmate-optimizer \
  --image ${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${GCP_REPO}/solmate-optimizer:latest \
  --region ${GCP_REGION} \
  --set-env-vars SOLMATE_SERIAL=your-serial,SOLMATE_PASSWORD=your-password,OWM_API_KEY=your-key \
  --memory 512Mi \
  --task-timeout 120 \
  --max-retries 1
```

For sensitive credentials, use [Secret Manager](https://cloud.google.com/run/docs/configuring/services/secrets) instead of plain env vars.

## 3. Test the job

```bash
gcloud run jobs execute solmate-optimizer --region ${GCP_REGION} --wait
```

Check logs:

```bash
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=solmate-optimizer" \
  --limit=50 --format="value(textPayload)" --project=${GCP_PROJECT} | tac
```

## 4. Schedule hourly runs

```bash
gcloud scheduler jobs create http solmate-optimizer-hourly \
  --location ${GCP_REGION} \
  --schedule "5 * * * *" \
  --time-zone "Europe/Vienna" \
  --uri "https://${GCP_REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${GCP_PROJECT}/jobs/solmate-optimizer:run" \
  --http-method POST \
  --oauth-service-account-email ${GCP_PROJECT}@appspot.gserviceaccount.com
```

This runs at 5 minutes past every hour (Vienna time). The script only writes to the SolMate if the profile actually changed.

## 5. Update the container

After code changes, rebuild and the job picks up `:latest` on next execution:

```bash
gcloud builds submit \
  --tag ${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${GCP_REPO}/solmate-optimizer
```

## Network

The container needs outbound access to:

| Host | Port | Protocol |
|------|------|----------|
| `sol.eet.energy` | 9124 | WebSocket (SolMate cloud API) |
| `api.awattar.at` | 443 | HTTPS (electricity prices) |
| `api.openweathermap.org` | 443 | HTTPS (weather forecast) |

Cloud Run allows outbound traffic by default — no VPC configuration needed.
