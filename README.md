# Automation Orchestrator Deployment

Deploys Automation Orchestrator on OpenShift via OLM, with CloudNativePG for PostgreSQL and
optional AAP integration.

**Full documentation: [documentation.md](documentation.md)** — variables, API reference, gotchas,
known gaps, [validation history](documentation.md#validation-history). [PLAN.md](PLAN.md) is the
original design plan and is historical only.

The earlier deployment was validated end to end on 2026-10-06. The explicit-target changes
are covered by local HTTPS tests; they have not been deployed to OpenShift. See
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
| `vars/vault.yml.example` | Optional encrypted launch-input template; never loaded implicitly |
| `tasks/validate-aap-target.yml` | Shared validation of the explicitly selected AAP target |
| `tests/test_aap_target.py` | Local HTTPS tests for target selection and cleanup failure handling |
| `design_decisions_history.md` | Decisions and implementation commit references |
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

### 2. Provide the intended AAP at launch

The playbooks no longer read the repository's `vars/vault.yml`. For AAP integration, supply
`aap_gateway_url`, `aap_admin_username`, and `aap_admin_password` together through a password
survey, credential injection, or an explicitly selected encrypted variables file. Omit all three
(or leave all three empty) to deploy Orchestrator standalone.

The target must be an HTTPS origin, for example `https://aap.example.com`. It must be reachable
and trusted by the OpenShift pods; an internal URL works when the cluster can reach and resolve it.
AAP certificate validation defaults to `true`. Cleanup also accepts `aap_ca_path` for a private CA
file in the execution environment. That file does not install trust into Orchestrator pods.

For CLI use, copy `vars/vault.yml.example` outside this checkout, add the desired launch inputs,
and encrypt the copy with `ansible-vault`. Do not put passwords into shell arguments.

### 3. Deploy Orchestrator

```bash
ansible-playbook deploy-automation-orchestrator.yml --ask-vault-pass \
  -e @/path/to/launch.vault.yml
```

The encrypted launch file must also supply `ocp_api_url` and `ocp_admin_password`.

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
5. **Survey or credential injection**: Provide `ocp_api_url` and `ocp_admin_password`, plus
   `ocp_admin_username` when it differs from `admin`. For AAP integration, also provide
   `aap_gateway_url`, `aap_admin_username`, and `aap_admin_password`; use password fields for
   passwords. Leave all three AAP inputs empty for a standalone deployment.
6. Attach a Vault credential only if you explicitly load a launch-time Vault file. The old
   repository `vars/vault.yml` no longer selects the target or requires a credential.

### Cleanup between deployments

When tearing down an OCP cluster and redeploying to a new one, the Orchestrator `setup_aap_oidc`
step will fail because the OAuth2 app ("Syntara") from the old cluster still exists on AAP. Run the
cleanup job template first:

- **Playbook**: `cleanup-aap-orchestrator.yml`
- **Inputs**: The same explicit AAP URL and admin credentials, plus
  `aap_cleanup_application_names`, a list of exact application names (for example `["Syntara"]`).
  A survey textarea can provide the list as YAML or JSON.
- **What it does**: Deletes only uniquely matched names on that target. Duplicate or unexpected
  matches stop cleanup before application deletion. It uses a temporary Gateway token and revokes
  it on success or failure; it never searches by substring.
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
An existing AAP identity provider or integration for a different URL stops the run rather than
silently retaining the old target. Rebinding an existing Orchestrator is a separate migration.

## Known gaps

The PostgreSQL image version is unpinned and the EE is larger than it needs to be — see
[Known gaps / TODO](documentation.md#known-gaps--todo).

## Validation

With Ansible, Python, and OpenSSL already available:

```bash
python3 -m unittest discover -s tests -v
ANSIBLE_LOCAL_TEMP=/tmp/orchestrator-ansible-local ansible-playbook --syntax-check -i localhost, cleanup-aap-orchestrator.yml
```

The tests start only a local HTTPS Gateway double, trust its generated test certificate, and run
actual Ansible cleanup tasks. They check target selection, exact matching, missing applications,
ambiguous results, deletion failure, token revocation, and rejection of unsafe inputs before any
network request. They do not contact AAP or OpenShift.
