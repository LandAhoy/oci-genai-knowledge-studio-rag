# Deployment guide

This guide deploys OCI Generative AI Knowledge Studio without storing runtime
credentials in Git.

## Prerequisites

- An OCI Linux VM with Docker Compose and Python 3.
- An OCI dynamic group and IAM policy allowing the VM Instance Principal to use
  Generative AI inference in the target compartment.
- The OCI model identifiers enabled in the selected region:
  `cohere.command-a-03-2025`, `cohere.command-a-vision`, and
  `cohere.embed-v4.0`.

## 1. Configure the OCI adapter

```sh
sudo mkdir -p /opt/oci-genai-adapter
sudo cp deployment/oci-genai-adapter/app.py /opt/oci-genai-adapter/app.py
sudo python3 -m venv /opt/oci-genai-adapter/venv
sudo /opt/oci-genai-adapter/venv/bin/pip install -r deployment/oci-genai-adapter/requirements.txt
sudo chown -R opc:opc /opt/oci-genai-adapter
sudo cp deployment/oci-genai-adapter/oci-genai-adapter.env.example /etc/oci-genai-adapter.env
sudo chmod 600 /etc/oci-genai-adapter.env
sudoedit /etc/oci-genai-adapter.env
sudo cp deployment/oci-genai-adapter/oci-genai-adapter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oci-genai-adapter
```

Confirm that `curl http://127.0.0.1:8181/v1/models` returns the configured
model identifiers. Do not expose port 8181 publicly.

## 2. Start the application stack

```sh
./scripts/bootstrap-local-env.sh
docker build -t ragflow-oci:v0.27.1-oci-embed-markdown-brand-v2 .
cd docker
docker compose -f docker-compose.yml -f docker-compose.oci.yml up -d
```

The OCI override maps `host.docker.internal` to the Docker host. Configure the
OpenAI-compatible model provider with base URL
`http://host.docker.internal:8181/v1`.

## 3. Configure models

In the application model-provider settings, add the OCI adapter models and set
the defaults appropriate to the workspace:

- chat: `cohere.command-a-03-2025`;
- vision: `cohere.command-a-vision`; and
- embedding: `cohere.embed-v4.0`.

The included embedding integration sends `SEARCH_DOCUMENT` during indexing and
`SEARCH_QUERY` during retrieval. Existing collections should be re-indexed only
after confirming their intended embedding model and dimension.

## 4. Validate

```sh
docker inspect docker-ragflow-cpu-1 --format '{{.State.Status}}'
curl -I http://127.0.0.1/
curl http://127.0.0.1:8181/v1/models
```

For application validation, use non-sensitive sample documents and confirm
document indexing, retrieval, model response, citations, and Markdown list
rendering.

## Security and operations

- Keep `/etc/oci-genai-adapter.env` and `docker/.env` local to the VM or inject
  them through the organization's secret manager.
- Do not commit OCI configuration, private keys, user documents, database files,
  object storage, indexes, logs, or credentials.
- Restrict public ingress to the intended HTTPS endpoint. Keep adapter and data
  services on private networks where possible.
- Preserve a tested image tag before upgrades to support rollback.
