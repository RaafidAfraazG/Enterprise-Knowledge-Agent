#!/usr/bin/env bash
# Run in Azure Cloud Shell, from a clone of this repository. Never enable xtrace.
set -euo pipefail
umask 077

RG=${RG:-kagent-rg}
LOCATION=${LOCATION:-eastus}
ENV_NAME=${ENV_NAME:-kagent-env}
APP_NAME=${APP_NAME:-kagent}
for name in "$RG" "$APP_NAME"; do
  if [[ ! "$name" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo 'RG and APP_NAME must use only letters, digits, underscores, or hyphens.' >&2
    exit 1
  fi
done
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/kagent-bootstrap"
mkdir -p "$state_dir"
acr_file="$state_dir/${RG}-${APP_NAME}.acr-name"
if [[ -z ${ACR_NAME:-} ]]; then
  if [[ -s "$acr_file" ]]; then
    ACR_NAME=$(cat "$acr_file")
  else
    suffix=$(python3 -c 'import secrets; print(secrets.token_hex(5))')
    ACR_NAME="kagent${suffix}"
    printf '%s\n' "$ACR_NAME" > "$acr_file"
  fi
fi
if [[ ! "$ACR_NAME" =~ ^[a-zA-Z0-9]{5,50}$ ]]; then
  echo 'ACR_NAME must be 5–50 alphanumeric characters.' >&2
  exit 1
fi
printf 'Using ACR_NAME=%s. Save this name for future runs.\n' "$ACR_NAME"

: "${AZURE_OPENAI_API_VERSION:=2024-02-01}"
for variable in AZURE_OPENAI_ENDPOINT AZURE_OPENAI_CHAT_DEPLOYMENT AZURE_OPENAI_EMBED_DEPLOYMENT QDRANT_URL; do
  if [[ -z ${!variable:-} ]]; then
    read -r -p "Enter ${variable}: " "$variable"
  fi
  if [[ -z ${!variable} ]]; then
    echo "${variable} must not be empty." >&2
    exit 1
  fi
done
read -r -s -p 'Azure OpenAI key (hidden): ' AZURE_OPENAI_API_KEY
printf '\n'
read -r -s -p 'Qdrant API key (hidden): ' QDRANT_API_KEY
printf '\n'
if [[ -z "$AZURE_OPENAI_API_KEY" || -z "$QDRANT_API_KEY" ]]; then
  echo 'Both keys are required. Nothing has been provisioned.' >&2
  exit 1
fi

APP_PROFILE=${APP_PROFILE:-baseline}
LANGFUSE_HOST=${LANGFUSE_HOST:-https://cloud.langfuse.com}
if [[ "$APP_PROFILE" != baseline && "$APP_PROFILE" != optimized ]]; then
  echo 'APP_PROFILE must be baseline or optimized.' >&2
  exit 1
fi
read -r -p 'Optional Azure router deployment (empty skips): ' AZURE_OPENAI_ROUTER_DEPLOYMENT
read -r -s -p 'Optional Langfuse public key (empty skips): ' LANGFUSE_PUBLIC_KEY
printf '\n'
LANGFUSE_SECRET_KEY=''
if [[ -n "$LANGFUSE_PUBLIC_KEY" ]]; then
  read -r -s -p 'Langfuse secret key (hidden; empty skips tracing): ' LANGFUSE_SECRET_KEY
  printf '\n'
fi

subscription=$(az account show --query id --output tsv)
for provider in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry; do
  az provider register --namespace "$provider" --wait --output none
done
az extension add --name containerapp --upgrade --only-show-errors
az group create --name "$RG" --location "$LOCATION" --output none
if ! az acr show --name "$ACR_NAME" --resource-group "$RG" --output none 2>/dev/null; then
  az acr create --name "$ACR_NAME" --resource-group "$RG" --location "$LOCATION" \
    --sku Basic --admin-enabled true --output none
else
  az acr update --name "$ACR_NAME" --resource-group "$RG" --admin-enabled true --output none
fi
if ! az containerapp env show --name "$ENV_NAME" --resource-group "$RG" --output none 2>/dev/null; then
  az containerapp env create --name "$ENV_NAME" --resource-group "$RG" \
    --location "$LOCATION" --output none
fi
if ! az containerapp show --name "$APP_NAME" --resource-group "$RG" --output none 2>/dev/null; then
  az containerapp create --name "$APP_NAME" --resource-group "$RG" --environment "$ENV_NAME" \
    --image mcr.microsoft.com/k8se/quickstart:latest --target-port 80 --ingress external \
    --min-replicas 1 --max-replicas 1 --output none
fi
az containerapp secret set --name "$APP_NAME" --resource-group "$RG" --secrets \
  "azure-openai-key=$AZURE_OPENAI_API_KEY" "qdrant-key=$QDRANT_API_KEY" --output none
az containerapp update --name "$APP_NAME" --resource-group "$RG" --set-env-vars \
  'LLM_PROVIDER=azure' "AZURE_OPENAI_ENDPOINT=$AZURE_OPENAI_ENDPOINT" \
  'AZURE_OPENAI_API_KEY=secretref:azure-openai-key' \
  "AZURE_OPENAI_CHAT_DEPLOYMENT=$AZURE_OPENAI_CHAT_DEPLOYMENT" \
  "AZURE_OPENAI_EMBED_DEPLOYMENT=$AZURE_OPENAI_EMBED_DEPLOYMENT" \
  "AZURE_OPENAI_API_VERSION=$AZURE_OPENAI_API_VERSION" "QDRANT_URL=$QDRANT_URL" \
  'QDRANT_API_KEY=secretref:qdrant-key' 'QDRANT_COLLECTION=company_docs' \
  --min-replicas 1 --max-replicas 1 --output none
az containerapp update --name "$APP_NAME" --resource-group "$RG" --set-env-vars \
  "PROFILE=$APP_PROFILE" "LANGFUSE_HOST=$LANGFUSE_HOST" \
  "AZURE_OPENAI_ROUTER_DEPLOYMENT=$AZURE_OPENAI_ROUTER_DEPLOYMENT" --output none
if [[ -n "$LANGFUSE_PUBLIC_KEY" && -n "$LANGFUSE_SECRET_KEY" ]]; then
  az containerapp secret set --name "$APP_NAME" --resource-group "$RG" --secrets \
    "langfuse-public=$LANGFUSE_PUBLIC_KEY" "langfuse-secret=$LANGFUSE_SECRET_KEY" --output none
  az containerapp update --name "$APP_NAME" --resource-group "$RG" --set-env-vars \
    'LANGFUSE_PUBLIC_KEY=secretref:langfuse-public' \
    'LANGFUSE_SECRET_KEY=secretref:langfuse-secret' --output none
fi

registry_user=$(az acr credential show --name "$ACR_NAME" --query username --output tsv)
registry_password=$(az acr credential show --name "$ACR_NAME" --query 'passwords[0].value' --output tsv)
az containerapp registry set --name "$APP_NAME" --resource-group "$RG" \
  --server "${ACR_NAME}.azurecr.io" --username "$registry_user" \
  --password "$registry_password" --output none
unset registry_password

credential_file="$state_dir/${RG}-${APP_NAME}.azure-credentials.json"
if [[ ! -s "$credential_file" ]]; then
  temporary=$(mktemp "$state_dir/credentials.XXXXXX")
  trap 'rm -f "${temporary:-}"' EXIT
  az ad sp create-for-rbac --name "${APP_NAME}-${RG}-github" --role Contributor \
    --scopes "/subscriptions/$subscription/resourceGroups/$RG" --sdk-auth > "$temporary"
  python3 -m json.tool "$temporary" >/dev/null
  mv "$temporary" "$credential_file"
fi

# Printing these values is intentional: the operator must copy them into GitHub.
# This script must not run in Actions or in a recorded/shared terminal session.
printf '\n=== GitHub Actions repository secrets (Settings > Secrets and variables > Actions) ===\n'
printf 'AZURE_OPENAI_API_KEY: %s\nAZURE_OPENAI_ENDPOINT: %s\n' "$AZURE_OPENAI_API_KEY" "$AZURE_OPENAI_ENDPOINT"
printf 'QDRANT_API_KEY: %s\nQDRANT_URL: %s\n' "$QDRANT_API_KEY" "$QDRANT_URL"
printf 'AZURE_CREDENTIALS (copy the entire JSON object):\n'
cat "$credential_file"
printf '\n=== GitHub Actions repository variables ===\n'
printf 'AZURE_RG: %s\nAZURE_ACR_NAME: %s\nAZURE_CONTAINERAPP_NAME: %s\n' "$RG" "$ACR_NAME" "$APP_NAME"
printf 'AZURE_OPENAI_CHAT_DEPLOYMENT: %s\nAZURE_OPENAI_EMBED_DEPLOYMENT: %s\n' \
  "$AZURE_OPENAI_CHAT_DEPLOYMENT" "$AZURE_OPENAI_EMBED_DEPLOYMENT"
printf 'AZURE_OPENAI_API_VERSION: %s\nEVAL_MIN_ACCURACY: 0.70\n' "$AZURE_OPENAI_API_VERSION"
printf '\nCredentials are retained privately at %s for idempotent reruns.\n' "$credential_file"
printf 'Copy secrets securely, clear the terminal, and keep this file private.\n'
unset AZURE_OPENAI_API_KEY QDRANT_API_KEY

printf '\nOptional GitHub variables:\nAPP_PROFILE: %s\nLANGFUSE_HOST: %s\nAZURE_OPENAI_ROUTER_DEPLOYMENT: %s\n' \
  "$APP_PROFILE" "$LANGFUSE_HOST" "$AZURE_OPENAI_ROUTER_DEPLOYMENT"
printf 'BENCH_LATENCY_N: 100\nBENCH_MAX_ACCURACY_DROP: 0.02\n'
if [[ -n "$LANGFUSE_PUBLIC_KEY" && -n "$LANGFUSE_SECRET_KEY" ]]; then
  printf 'Optional GitHub secrets:\nLANGFUSE_PUBLIC_KEY: %s\nLANGFUSE_SECRET_KEY: %s\n' \
    "$LANGFUSE_PUBLIC_KEY" "$LANGFUSE_SECRET_KEY"
fi
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY
