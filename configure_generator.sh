#!/bin/bash
set -euo pipefail
cd /home/ubuntu/ireland-news
ENV=.generator.env
printf 'LLM base URL [https://api.deepseek.com/v1]: '
read -r BASE_URL
BASE_URL=${BASE_URL:-https://api.deepseek.com/v1}
printf 'LLM model [deepseek-chat]: '
read -r MODEL
MODEL=${MODEL:-deepseek-chat}
printf 'LLM API key (hidden): '
read -rs API_KEY
printf '\n'
if [ -z "$API_KEY" ]; then
  echo "API key cannot be empty" >&2
  exit 1
fi
umask 077
cat > "$ENV" <<EOF
LLM_BASE_URL=$BASE_URL
LLM_MODEL=$MODEL
LLM_API_KEY=$API_KEY
EOF
chmod 600 "$ENV"
unset API_KEY
echo "Saved $ENV with mode 600."
echo "Validating provider connectivity..."
./.venv/bin/python - <<'PY'
import os, json, requests
from pathlib import Path
for raw in Path('.generator.env').read_text().splitlines():
    if '=' in raw:
        k,v=raw.split('=',1); os.environ[k]=v
base=os.environ['LLM_BASE_URL'].rstrip('/')
payload={"model":os.environ['LLM_MODEL'],"messages":[{"role":"user","content":"Reply only with OK."}],"temperature":0}
r=requests.post(base+'/chat/completions',headers={"Authorization":"Bearer "+os.environ['LLM_API_KEY'],"Content-Type":"application/json"},json=payload,timeout=30)
r.raise_for_status()
data=r.json()
text=data['choices'][0]['message']['content'].strip()
print("LLM connectivity OK:", text[:20])
PY
