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
- The receiving AAP's approved Core 2.20 execution environment, including the
  Kubernetes Python client and jsonpatch, plus its existing certified Hub credentials
  for normal project requirements

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
| `collections/requirements.yml` | Pinned OpenShift/Kubernetes collections installed by normal AAP project sync |
| `execution-environment/requirements.yml` | Matching retained builder/sync input; runtime uses native project requirements |
| `execution-environment/execution-environment.yml` | EE base image reference |
| `documentation.md` | Full documentation |
| `PLAN.md` | Original design plan (historical) |

## Usage

### 1. Use the receiving AAP's configured execution environment

The CaC importer binds this project to the receiving AAP's approved Core 2.20
runtime with the Kubernetes Python client and jsonpatch. Collections are supplied
through `collections/requirements.yml`: `redhat.openshift` 6.0.0 and
`kubernetes.core` 6.6.0. Normal project sync obtains them through the organization's
existing certified Hub credentials. No additional image, credential or manual
package installation is introduced by this project.

For a separately managed CLI runtime, ensure those Python dependencies and
collections are already available before running the playbook.

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

Keep `secure_logging: true` for deployments. Authentication and deployment results are
protected, including token facts and Kubernetes Secret responses. The deployment summary
does not print the generated administrator password; retrieve it through the authorized
OpenShift Secret access described in the product documentation.

The deployment survey includes a required **Disable TLS verification** choice,
defaulting to **No**. No, or an omitted CLI answer, verifies the OpenShift API,
OAuth, Orchestrator API and AAP integration certificates. An explicit **Yes** skips
those certificate checks for that one deployment; HTTPS encryption and protected
logging remain enabled. Legacy verification flags cannot silently override No.
Never save Yes in the job template's extra variables or change the CaC default.
Each deployment against unverified endpoints must explicitly select Yes again.
For CLI launches, quote `disable_tls_verification: 'Yes'` in protected launch
variables. AAP can serialize the choice as a genuine boolean; true/false retain
the Yes/No meanings. Lowercase strings, numbers and other values are rejected.

For a cluster API signed by its own CA, supply the trusted PEM bundle as `ocp_ca_bundle`
in the protected launch variables. The playbook writes it to a temporary execution-environment
file, passes that file to the native Kubernetes modules, and removes it on success or failure.
For a privately signed Orchestrator route, `orchestrator_ca_path` can name its trusted CA
file already available inside the execution environment. Obtain CA material from the cluster
administrator or an authenticated trusted source; never treat an unverified downloaded
certificate as established trust.

### Running as an AAP Job Template

1. **Project**: Create a project pointing at this repo. Enable `scm_update_on_launch: true` so the
   playbook always runs the latest version.
2. **Execution Environment**: Use the receiver's configured Core 2.20 runtime and
   the organization's existing certified Hub credentials as described above.
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
