# Self-serve Azure setup

The repository works locally with fake providers without an Azure account, Qdrant
cluster, secrets, or Docker. Start with the local instructions in [README.md](../README.md).
This guide enables the optional real-provider evaluation and deployment pipeline.
Provisioning creates billable resources; a free Qdrant tier does not make Azure free.
Nothing is tied to the original author's subscription or GitHub account.

## 1. Create an Azure subscription

Create an account at the Azure portal and select a subscription with billing enabled.
Use an account allowed to create resource groups and service principals, register
resource providers, and assign Contributor on a resource group. Tenant administrators
may need to allow service-principal creation. Set a budget alert before provisioning.
Open Azure Cloud Shell in **Bash** mode, select the subscription with
`az account set --subscription <your-subscription-id>`, and confirm `az account show`.

## 2. Create Azure OpenAI resources and deployments

In the Azure portal / Azure AI Foundry, create an Azure OpenAI resource in a region
supported by your subscription and desired models. Deploy a chat model supporting
chat completions (for example a compatible GPT-4o deployment) and a text embedding
model (for example text-embedding-3-small). Deployment names are names **you choose**,
not necessarily model names. This code uses ordinary chat completions with
`temperature=0`; choose a model/API version that supports that interface.

Record the resource HTTPS endpoint, key from **Keys and Endpoint**, both deployment
names, and a supported API version. The default `2024-02-01` may need updating for
your resource/model. Never put keys in source control or issue comments. Check the
resource's quota and model availability before running a 200-question evaluation.

## 3. Create Qdrant Cloud

Create a free cluster in Qdrant Cloud if available for your account. Copy its HTTPS
cluster endpoint and a database API key with collection read/write/delete permission.
The HTTPS URL often includes port 6333; do not use the dashboard URL. Keep the key
private. The application creates its collection and discovers the embedding dimension.
Use a separate collection whenever you change embedding models.

## 4. Run the bootstrap script in Cloud Shell

Clone **your** fork/repository in Cloud Shell and change to its root. Run:

```bash
# Optional names; export these again on later runs to reuse the same resources.
export RG=kagent-rg
export LOCATION=eastus
export ENV_NAME=kagent-env
export APP_NAME=kagent
# Optional globally unique, 5–50 alphanumeric name:
# export ACR_NAME=youruniqueregistryname
bash scripts/azure_bootstrap.sh
```

Choose a permitted region instead of `eastus` if necessary. The script prompts for
endpoint, deployment names, and Qdrant URL when not already exported. Azure OpenAI
and Qdrant keys are always read with hidden input. You can export
`AZURE_OPENAI_API_VERSION` before running to override the default.

It creates or reuses a resource group, Basic ACR with admin enabled, Container Apps
environment, and a placeholder app using the quickstart image on external port 80
with min/max replicas 1. It stores the two API keys as Container App secrets and
references them from environment variables. It configures ACR image-pull credentials
and creates an RG-scoped Contributor service principal for Actions. The workflow
changes ingress to port 8000 when deploying the actual image.

If no ACR name was supplied, the script generates and prints one. Save it. The name
and service-principal JSON are retained under
`${XDG_STATE_HOME:-$HOME/.local/state}/kagent-bootstrap` for repeatable runs with the
same RG and app names. Do not delete that state casually: losing the credential
cache may require rotating a service principal's credentials and updating GitHub.
Changing names creates a different set of resources. Reruns reuse existing services
and update configuration; they do not intentionally replace deployed app images.

**The final script output intentionally includes sensitive GitHub secret values,
including AZURE_CREDENTIALS JSON.** Run in a private, unrecorded Cloud Shell session,
never in Actions or with `bash -x`. Copy values directly to GitHub, clear the terminal,
and protect the credential cache (created with private permissions). Runtime keys are
stored as Container App secrets, not literal application environment values.

## 5. Configure your GitHub repository

Open **Settings → Secrets and variables → Actions**. Use the Secrets tab for secret
values and the Variables tab for non-secret settings. Repository-level values are
sufficient; no protected GitHub environment is required.

| Type | Name | Where to find the value |
| --- | --- | --- |
| Secret | `AZURE_OPENAI_API_KEY` | Azure OpenAI resource → Keys and Endpoint; bootstrap output |
| Secret | `AZURE_OPENAI_ENDPOINT` | Azure resource HTTPS endpoint; bootstrap output |
| Secret | `QDRANT_URL` | Qdrant cluster HTTPS endpoint; bootstrap output |
| Secret | `QDRANT_API_KEY` | Qdrant Cloud database API key; bootstrap output |
| Secret | `AZURE_CREDENTIALS` | Entire JSON object printed by bootstrap (includes clientSecret) |
| Variable | `AZURE_RG` | RG printed by bootstrap |
| Variable | `AZURE_ACR_NAME` | Registry name printed by bootstrap; no `.azurecr.io` suffix |
| Variable | `AZURE_CONTAINERAPP_NAME` | APP_NAME printed by bootstrap |
| Variable | `AZURE_OPENAI_CHAT_DEPLOYMENT` | Your exact chat deployment name |
| Variable | `AZURE_OPENAI_EMBED_DEPLOYMENT` | Your exact embedding deployment name |
| Variable | `AZURE_OPENAI_API_VERSION` | Supported API version; default `2024-02-01` |
| Variable | `EVAL_MIN_ACCURACY` | Minimum passing accuracy; default `0.70` |

GitHub evaluation settings and deployed runtime settings are separate. If you rotate
keys or change deployments, update GitHub **and** rerun bootstrap with the same
resource names and the new configuration. A deployment alone does not synchronize
all runtime environment variables. `AZURE_CREDENTIALS` grants Contributor only on
this resource group; rotate it according to your organization's policy and refresh
the private cache when doing so. ACR admin access is a simple demo configuration;
use managed identities for a hardened production deployment.

## 6. Push and inspect Actions

Push any branch or open a pull request. The `test` job installs Python 3.11, lints,
runs offline tests, and builds the Docker image with no secrets. After that:

- `eval` checks for the four cloud secrets first. If any are missing, it prints
  **Cloud credentials not configured, skipping eval. See docs/SETUP.md** and writes
  the same message to the job summary. Cloud work is skipped and the gate job passes.
- With credentials, the smoke test checks real chat, embeddings, and Qdrant. Evaluation
  uses `eval_<GitHub run ID>`, never the production `company_docs` collection. The
  smoke test uses and deletes its own random temporary collection. Evaluation
  collections are retained for diagnosis; remove old `eval_*` collections as needed.
- Evaluation writes `eval/results/latest.json`, appends metrics to the job summary,
  and uploads a report artifact even when the accuracy gate fails (if a report exists).
  A failure during initial service setup may leave no report; read the failed step.
- Only pushes to `main` can deploy, and evaluation must have succeeded. Deploy checks
  the cloud secrets, AZURE_CREDENTIALS, and all three resource variables. Missing
  deployment configuration produces a clean skip notice, not a failure. Without
  any cloud secrets, both cloud stages skip their work and CI still passes.
- Configured deploys log into Azure, build/push commit-SHA and `latest` image tags
  through ACR, update the app, then retry real `/health` and document `/ask` requests.
  The job summary prints the live URL only after both checks pass. Deploys serialize.

Fork pull requests do not receive repository secrets, so cloud stages skip. Review
changes before granting cloud credentials to untrusted code. The deployed Phase 2
API has external ingress and no authentication; use only fictional demo data.

## 7. Troubleshooting

| Symptom | What to check |
| --- | --- |
| Deployment not found / 404 | Exact deployment name, resource endpoint, and whether deployment completed |
| Unsupported API version / 400 | Set AZURE_OPENAI_API_VERSION to a version supported by both deployments |
| 429 quota/rate limit | Model token quota and requests per minute; runner retries rate limits up to 3 attempts; lower `--workers` for a manual run |
| Region not allowed / model unavailable | Subscription region policy and model availability; override LOCATION |
| ACR name taken | Supply a new globally unique ACR_NAME, save it, and use it on reruns |
| Qdrant 401 / 403 | Cluster database key, HTTPS endpoint, key permissions; update both GitHub and Container App secrets |
| Qdrant dimension mismatch | New embedding model needs a new empty collection; do not reuse old vectors |
| Bootstrap authorization failure | Active subscription, tenant SP creation policy, and rights to assign an RG role |
| Image pull failure | ACR admin enabled and app registry credentials configured; rerun bootstrap |
| Ingress returns 502 after deploy | Target port 8000, container startup logs, runtime keys, deployment names, and Qdrant access |
| Eval fails below threshold | Inspect full JSON failures; do not lower the threshold just to hide real regressions |

No Docker, Azure CLI, real credentials, or external services are used by pytest.
Docker image execution, Actions service semantics, real Azure/Qdrant connectivity,
ACR tasks, role assignments, and Container Apps deployment require GitHub/Azure
validation; local YAML and shell checks cannot prove those integrations work.

When finished experimenting, delete the demo resource group in the Azure portal to
stop Azure resource costs, and remove the associated service principal and GitHub
credentials if no longer needed. Keep unrelated resource groups intact.

## Optional: Langfuse (tracing)

Create a free Langfuse Cloud account and project. Open the project's API keys page,
create a public/secret key pair, and note the host for the project region (for example
`https://cloud.langfuse.com`; use the host shown by your project). Keep the secret
key private. The app pins Langfuse SDK **2.60.10**; upgrading needs API review and tests.

The Cloud Shell bootstrap now asks for optional Langfuse keys and an optional cheaper
Azure router deployment. Leave the Langfuse public key empty to skip tracing; a
missing secret key also disables tracing on a fresh deployment. Provided keys are
stored as Container App secrets and supplied through secret references. `APP_PROFILE`
defaults to baseline; export it and `LANGFUSE_HOST` before rerunning bootstrap.
Existing keys are preserved when optional prompts are skipped. To disable existing
tracing explicitly, set `TRACING_ENABLED=false` on the Container App, or remove both
key environment bindings. Missing Langfuse keys never prevent CI/evaluation/deployment.

| Type | Name | Purpose / value |
| --- | --- | --- |
| Secret | `LANGFUSE_PUBLIC_KEY` | Project public key |
| Secret | `LANGFUSE_SECRET_KEY` | Project secret key |
| Variable | `LANGFUSE_HOST` | Host shown by your project's region |
| Variable | `AZURE_OPENAI_ROUTER_DEPLOYMENT` | Optional cheaper chat deployment, created in your Azure OpenAI resource |
| Variable | `APP_PROFILE` | `baseline` (default) or `optimized`; bootstrap sets Container App `PROFILE` |
| Variable | `BENCH_LATENCY_N` | Sequential sample size; default 100 |
| Variable | `BENCH_MAX_ACCURACY_DROP` | Maximum allowed accuracy loss as a fraction; default 0.02 |
| Variable | `PRICE_CHAT_INPUT_PER_M` | Optional actual chat input USD/million token rate |
| Variable | `PRICE_CHAT_OUTPUT_PER_M` | Optional actual chat output USD/million token rate |
| Variable | `PRICE_EMBED_PER_M` | Optional actual embedding USD/million token rate |

The application recognizes `PROFILE`, `OPT_ROUTER_MODEL`, `OPT_CACHE`, `OPT_PARALLEL`,
`OPT_TOP_K`, `OPT_SHORT_PROMPTS`, `CACHE_TTL_SECONDS`, `TRACING_ENABLED`, and the price
fields as environment variables. Set individual overrides on the Container App when
needed. Run bootstrap again to apply an updated APP_PROFILE/router/host to the app;
GitHub repository variables alone do not rewrite runtime configuration.

Tracing uses a bounded background exporter, so outages drop telemetry rather than
holding requests for network calls. Shutdown and evaluation attempt a short bounded
flush; delivery is best effort. Questions/context/answers appear in traces. Cost
fields are estimates using model-price defaults or your overrides, not invoices.

## Run the real paired benchmark

After configuring Azure and Qdrant, open **Actions → Paired Azure benchmark → Run
workflow**, select the branch, and run it. This manual workflow requires no write
permission to the repository. Missing cloud credentials produce a clean skip notice.
Langfuse keys are optional. It uses an isolated `eval_bench_<run_id>` collection,
ingested once and shared by both profiles. Remove old eval collections when no
longer needed. A real run makes many paid model calls: full accuracy, warm-up, three
sequential latency passes per profile, and a separate repeat-traffic pass.

Download the `paired-benchmark-<run_id>` artifact. It contains `baseline.json`,
`optimized.json`, `comparison.json`, `comparison.md`, and a README rendered from those
measurements. The summary shows the paired table. A quality regression beyond the
configured limit fails the workflow but still uploads completed result files. Cold
latency and repeated-question cache results are separate scenarios; read
[METHODOLOGY.md](METHODOLOGY.md) before using either in a claim.

To update the checked-in README yourself, copy the three measured JSON files into
`eval/results/`, then run `python scripts/update_readme.py` and review the diff.
The workflow supplies the generated README as an artifact; it does not push commits.
Missing files or fake-provider reports leave the placeholder unchanged. The script
is idempotent and truncates display values without rounding them upward. To reproduce
an offline smoke check: `python -m eval.benchmark --provider fake --limit 20`.
