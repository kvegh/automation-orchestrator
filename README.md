# Automation Orchestrator Deployment

Deploys Automation Orchestrator on OpenShift via OLM, with CloudNativePG for PostgreSQL and
optional AAP integration.

**Full documentation: [documentation.md](documentation.md)** — variables, API reference, gotchas,
known gaps, [validation history](documentation.md#validation-history). [PLAN.md](PLAN.md) is the
original design plan and is historical only.

Last validated end to end on 2026-10-06, after this directory became its own repository — see
[Validation history](documentation.md#validation-history).

## Prerequisites

- OpenShift 4.14+ with the `redhat-operators` and `certified-operators` CatalogSources
- AAP 2.7+, reachable from the OCP cluster on port 443
- A default StorageClass that can provision a 10Gi RWO volume for PostgreSQL
- OCP cluster-admin credentials (provided at launch via job template survey)
- `ee-supported-rhel9` registered as an Execution Environment in AAP (includes all certified
  collections and the `kubernetes` Python library)

PostgreSQL is provisioned as a **single-instance CloudNativePG cluster — no HA, and not supported
by Red Hat**. This is a demo deployment. See
[Supported versions](documentation.md#supported-versions).

## Files

| File | Purpose |
|------|---------|
| `deploy-automation-orchestrator.yml` | Main playbook — operators, PG, CR, AAP integration |
| `cleanup-aap-orchestrator.yml` | Cleanup playbook — removes Orchestrator OAuth2 apps from AAP |
| `sync-collections.yml` | Unused in the current deployment — syncs collections to private automation hub. Kept for a future switch to `ee-minimal-rhel9` ([why](documentation.md#files)) |
| `vars/main.yml` | Default variables |
| `vars/vault.yml.example` | Template for secrets (copy to `vault.yml`, encrypt) |
| `execution-environment/requirements.yml` | Not read at runtime — collection list staged for the `ee-minimal-rhel9` switch ([why](documentation.md#what-execution-environmentrequirementsyml-is-for)) |
| `execution-environment/execution-environment.yml` | EE base image reference |
| `documentation.md` | Full documentation |
| `PLAN.md` | Original design plan (historical) |

## Usage

### 1. Register the EE in AAP

No custom build needed. The stock `ee-supported-rhel9` image includes the required certified
collections (`redhat.openshift`, `kubernetes.core`) and the `kubernetes` Python library.

In AAP, create an Execution Environment pointing at:
```
registry.redhat.io/ansible-automation-platform-27/ee-supported-rhel9:latest
```

**AAP containerized note:** The image must exist in AAP's service podman storage
(`~/aap/containers/storage`), not the interactive user's storage. If the image is only in
interactive storage, copy it:
```bash
podman save registry.redhat.io/.../ee-supported-rhel9:latest | podman --remote load
```

### 2. Create the vault file

```bash
cp vars/vault.yml.example vars/vault.yml
# Edit vars/vault.yml with your actual values
ansible-vault encrypt vars/vault.yml
```

The vault only contains AAP credentials (`aap_gateway_url`, `aap_admin_username`,
`aap_admin_password`). OCP credentials are provided at launch time via the job template survey —
they are never stored in the vault.

Omit `aap_gateway_url` from the vault to skip AAP integration (Orchestrator deploys standalone).

**Note:** `aap_gateway_url` must be reachable from the OCP cluster — use the public URL (e.g.,
`https://aap.example.com`), not an internal hostname that the remote OCP pods cannot resolve.

### 3. Deploy Orchestrator

```bash
ansible-playbook deploy-automation-orchestrator.yml --ask-vault-pass \
  -e ocp_api_url=https://api.cluster.example.com:6443 \
  -e ocp_admin_password=<password>
```

The playbook automatically obtains an OCP API token at runtime using the OAuth
`openshift-challenging-client` flow (with `X-CSRF-Token` header required by OCP 4.21+) — no manual
`oc login` needed.

`ocp_admin_username` defaults to `admin`; pass it explicitly if the cluster's cluster-admin account
is named differently.

The route hostname is derived from the API URL automatically. **That derivation assumes the
demo.redhat.com (RHPDS) URL convention** — `https://api.<cluster>.<domain>:6443` with apps on
`*.apps.<cluster>.<domain>`. On a cluster that does not follow it, pass `orchestrator_route_host`
explicitly. See [Route hostname derivation](documentation.md#route-hostname-derivation).

To debug credential-related task failures, pass `secure_logging: false` as an extra var — this
disables `no_log` on sensitive tasks so request/response bodies are visible in job output.

### Running as an AAP Job Template

1. **Project**: Create a project pointing at this repo. Enable `scm_update_on_launch: true` so the
   playbook always runs the latest version.
2. **Execution Environment**: Register `ee-supported-rhel9:latest` with pull policy `Never` (the
   image must already be in AAP's podman storage — see Step 1).
3. **Inventory**: Create an inventory with a single `localhost` host. Set
   `ansible_connection: local` as a host variable (the playbook also sets `connection: local`, so
   any inventory works).
4. **Job Template**: Create a job template using the project, EE, inventory, and
   `deploy-automation-orchestrator.yml` as the playbook.
5. **Vault Credential**: Attach an Ansible Vault credential to the job template (decrypts
   `vars/vault.yml` at runtime).
6. **Survey**: Enable a survey with:
   - `ocp_api_url` (text) — the OCP API URL, e.g.
     `https://api.cluster-xyz.dyn.redhatworkshops.io:6443`
   - `ocp_admin_password` (password) — the OCP admin password (stored encrypted, shown as
     `$encrypted$`)
   - `ocp_admin_username` (text, default `admin`) — optional, only if the cluster admin account is
     not `admin`
7. No custom credential types needed — AAP secrets live in the encrypted vault file, OCP
   credentials come from the survey.

### Cleanup between deployments

When tearing down an OCP cluster and redeploying to a new one, the Orchestrator `setup_aap_oidc`
step will fail because the OAuth2 app ("Syntara") from the old cluster still exists on AAP. Run the
cleanup job template first:

- **Playbook**: `cleanup-aap-orchestrator.yml`
- **What it does**: Finds and deletes all OAuth2 applications matching "syntara" or "orchestrator"
  from AAP Gateway
- **When to run**: Before deploying Orchestrator to a new OCP cluster, or after tearing down an old
  deployment

There is deliberately no OCP-side teardown playbook — the demo cluster is disposable and gets
destroyed and re-requested rather than cleaned up. See
[Cleanup and teardown](documentation.md#cleanup-and-teardown).

## What it does

1. Validates OCP version and CatalogSources
2. Installs CloudNativePG operator (Manual approval) in `cnpg-system`
3. Creates PG secrets (generates passwords on first run, reads existing on re-run)
4. Creates CloudNativePG Cluster with `orchestrator`, `temporal`, and `temporal_visibility`
   databases
5. Installs Orchestrator operator (Manual approval) in `automation-orchestrator`
6. Creates AutomationOrchestrator CR pointing at CloudNativePG
7. (Optional) Changes local admin email to avoid OIDC collision, configures OIDC identity provider
   and AAP integration via Orchestrator REST API

Step-by-step detail, expected pods and verification steps are in
[documentation.md](documentation.md#what-the-playbook-does).

## Idempotency

Re-running the playbook is safe. Secrets use a check-before-create pattern (passwords generated
only on the first run); identity provider, credential and integration are each checked via the
Orchestrator REST API before POST; OLM Subscriptions and InstallPlans are idempotent by nature.

## Known gaps

The PostgreSQL image version is unpinned and the EE is larger than it needs to be — see
[Known gaps / TODO](documentation.md#known-gaps--todo).
