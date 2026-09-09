# OCI Generative AI Knowledge Studio

> **Project owner and maintainer: HAFEEZ**

OCI Generative AI Knowledge Studio is a deployable, Oracle-branded enterprise
knowledge assistant. It combines document ingestion, configurable parsing and
chunking, hybrid retrieval, grounded chat, optional citations, agent workflows,
MCP integration, model-provider configuration, data-source connectors, and
chat-channel configuration.

The project integrates OCI Generative AI through an OpenAI-compatible adapter.
The tested OCI model configuration includes:

- `cohere.command-a-03-2025` for text chat;
- `cohere.command-a-vision` for vision-capable chat; and
- `cohere.embed-v4.0` at 1,536 dimensions, using `SEARCH_DOCUMENT` for indexing
  and `SEARCH_QUERY` for retrieval.

## Repository contents

- `web/`, `rag/`, `api/`, `mcp/`, `agent/`, and related directories: complete
  application source.
- `docker/`: local multi-service deployment manifests and OCI override.
- `deployment/oci-genai-adapter/`: OCI GenAI adapter source, requirements,
  service template, and secret-free environment template.
- `deployment/README.md`: secure end-to-end deployment instructions.
- `scripts/bootstrap-local-env.sh`: creates a local ignored runtime environment
  file with newly generated service passwords.

## Security boundary

This repository deliberately excludes live credentials, API keys, OCI config,
private keys, database volumes, object-store data, search indexes, chat history,
and uploaded documents. Do not commit a populated `.env` file.

Use the supplied templates and your organization's secret-management process.
The OCI adapter is designed to run with an OCI Instance Principal; grant that
principal only the OCI Generative AI permissions required for the configured
compartment and models.

## Start here

Read [deployment/README.md](deployment/README.md) before deployment. It covers
the required OCI IAM policy, adapter setup, Docker stack, model-provider setup,
validation, and rollback.

## Current deployment fixes

The OCI deployment image tag is `ragflow-oci:v0.27.1-oci-embed-markdown-brand-v5-retrieval`.
It includes two reliability fixes for OCI-backed knowledge-base chat:

- Newly indexed ordinary source chunks default to `available_int=1`, matching
  the retrieval filter while preserving intentionally disabled chunks.
- If a selected model does not support tool calls (for example, a vision model),
  a reasoning-enabled chat automatically uses the standard grounded retrieval
  path instead of answering without its attached knowledge base.

## Upstream notices

This repository retains the applicable upstream source license and notices in
[`LICENSE`](LICENSE) and source-file headers. The application-specific OCI,
embedding-role, Markdown-rendering, and branding changes are included in this
repository.
