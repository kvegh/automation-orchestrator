# Automation Orchestrator — Documentation

Deploys Red Hat Ansible Automation Orchestrator on OpenShift via OLM, with CloudNativePG for
PostgreSQL and AAP wired in as an OIDC identity provider and an automation integration.

Everything here describes the deployment **as built**. The original design plan is in
[PLAN.md](PLAN.md) — it is a historical planning artifact and several of its decisions were
superseded during implementation. This file is the current truth.

---

## Supported versions

From the Red Hat 2026.8 documentation (`automation-orchestrator-install`, `automation-orchestrator-plan`):

| Component | Supported version |
|---|---|
| OpenShift Container Platform | 4.14 or later (x86_64 and ARM64) |
| Ansible Automation Platform | 2.7 or later, reachable on port 443 |
| PostgreSQL | 15 |

No elevated cluster permissions are required beyond what OLM operator installation needs.

**CloudNativePG is not supported by Red Hat.** The install guide describes it as provided "for
convenience and prototyping" and directs support questions to the upstream CloudNativePG
partners. It is used here because this is a demo deployment. A production deployment brings its
own PostgreSQL 15 instance and skips the entire CNPG section.

---

## Architecture

```
 KVM host
   |
   +-- AAP VM (AAP 2.7 containerized) <--- automation gateway on port 443
   |
   +-- managed host VMs

 OCP cluster (remote, demo.redhat.com / RHPDS on AWS)
   |
   +-- cnpg-system namespace
   |     +-- CloudNativePG operator (OLM, certified-operators, channel stable-v1)
   |
   +-- automation-orchestrator namespace
         +-- Orchestrator operator (OLM, redhat-operators, channel stable)
         +-- AutomationOrchestrator CR
         +-- CloudNativePG Cluster (orchestrator-pg)
         +-- PostgreSQL secrets (generated on first run)
```

The only cross-cluster link is Orchestrator → AAP gateway over HTTPS/443. Nothing connects
inbound from AAP to OCP, so AAP does not need to be reachable from the internet in the inbound
direction — but it **does** need to be reachable *from* the OCP pods.

---

## Prerequisites

- OpenShift 4.14+ with the `redhat-operators` and `certified-operators` CatalogSources present
  (the playbook asserts both).
- A default StorageClass able to provision a 10Gi RWO volume for the PostgreSQL cluster
  (`cnpg_storage_size`). Red Hat's own template defaults to 5Gi; 10Gi is used here for headroom.
- `ee-supported-rhel9` registered as an execution environment in AAP. It already contains
  `redhat.openshift`, `kubernetes.core`, `ansible.platform` and the `kubernetes` Python library,
  so no custom EE build is needed.
- An AAP vault credential attached to the job template, to decrypt `vars/vault.yml` at runtime.

### CloudNativePG channel caveat

`cnpg_channel` defaults to `stable-v1`. This was validated on the OCP 4.21 catalog used by
demo.redhat.com. The channel name is a property of the catalog, not of the Orchestrator, so on an
older cluster the certified catalog may publish `cloudnative-pg` under `stable` instead. If
operator installation fails with `no operators found in channel stable-v1 of package
cloudnative-pg`, override `cnpg_channel`.

### AAP containerized EE storage

AAP containerized uses its own podman storage root (`~/aap/containers/storage`), separate from the
interactive user's storage. An image pulled or built as the interactive user is invisible to AAP's
receptor. Copy it across:

```bash
podman save registry.redhat.io/ansible-automation-platform-27/ee-supported-rhel9:latest \
    | podman --remote load
```

Then register the EE in AAP with pull policy `Never`.

---

## Variables

### Provided at launch (job template survey)

| Variable | Required | Notes |
|---|---|---|
| `ocp_api_url` | yes | e.g. `https://api.cluster-xyz.dyn.redhatworkshops.io:6443` |
| `ocp_admin_password` | yes | survey password field, stored as `$encrypted$` |
| `ocp_admin_username` | no | defaults to `admin`. Override if the cluster's cluster-admin account is named something else — otherwise token acquisition fails with a bare 401. |

OCP credentials are deliberately **never** stored in the vault. The playbook exchanges them for an
API token at runtime via the OCP OAuth `openshift-challenging-client` flow.

### Provided via `vars/vault.yml` (ansible-vault encrypted, committed)

| Variable | Notes |
|---|---|
| `aap_gateway_url` | Omit to skip AAP integration entirely — Orchestrator then deploys standalone. Must be the **public** URL: the Orchestrator pods resolve and connect to it from inside the OCP cluster, so an internal hostname will not work. |
| `aap_admin_username` | defaults to `admin` |
| `aap_admin_password` | |

### Route hostname derivation

`orchestrator_route_host` is derived automatically:

```
ocp_apps_domain       = ocp_api_url, with a literal "https://api." prefix
                        and a literal ":6443" suffix stripped
orchestrator_route_host = "orchestrator.apps." + ocp_apps_domain
```

**This form is built for demo.redhat.com (RHPDS) OCP instances**, whose API URLs are always
`https://api.<cluster>.<domain>:6443` with apps on `*.apps.<cluster>.<domain>`. Any cluster that
does not follow that convention — a different API hostname, a non-6443 port, a split apps domain —
will produce a wrong route host and the deployment will come up unreachable rather than failing
loudly. Set `orchestrator_route_host` explicitly as an extra var to bypass the derivation.

The same derivation builds the OAuth endpoint (`https://oauth-openshift.apps.<domain>`), so it has
to be right before anything else can work.

### Non-secret defaults (`vars/main.yml`)

`orchestrator_namespace`, `cnpg_namespace`, `orchestrator_channel`, `cnpg_channel`,
`orchestrator_operator_name`, `cnpg_operator_name`, `orchestrator_cr_name`, `cnpg_cluster_name`,
`cnpg_instances`, `cnpg_storage_size`, `pg_port`, `pg_ssl_mode`, `ocp_validate_certs`.

`ocp_validate_certs` is `false` because demo.redhat.com clusters use self-signed certificates.

`secure_logging` is not declared in `vars/main.yml` but is honoured throughout: every task that
carries a credential uses `no_log: "{{ secure_logging | default(true) }}"`. Pass
`-e secure_logging=false` to see request and response bodies when debugging an auth failure. Do
not leave it off — AAP job events capture `invocation.module_args.body`, which contains plaintext
passwords.

---

## What the playbook does

`deploy-automation-orchestrator.yml`, single play against `localhost`, in this order:

1. **Validate and authenticate** — assert `ocp_api_url` / `ocp_admin_password` are set, derive the
   apps domain and route host, obtain an OCP API token via the OAuth flow.
2. **Preflight** — assert OCP >= 4.14, assert both CatalogSources exist.
3. **CloudNativePG operator** — namespace, OperatorGroup, Subscription (`certified-operators`,
   channel `stable-v1`, Manual approval), approve the InstallPlan, wait for the deployment.
4. **Orchestrator namespace and secrets** — create the namespace; for each of
   `orchestrator-pg-credentials`, `temporal-pg-credentials` and `orchestrator-admin-password`,
   check whether the secret exists, generate a 32-character password only if it does not, and
   create it. On a re-run the existing passwords are read back instead.
5. **CloudNativePG Cluster** — create `orchestrator-pg` with `max_connections=200`, bootstrap the
   `orchestrator` database and create `temporal` / `temporal_visibility` plus the `temporal_user`
   role via `postInitApplicationSQL`. Wait for ready instances.
6. **Orchestrator operator** — OperatorGroup, Subscription (`redhat-operators`, channel `stable`,
   Manual approval), approve the InstallPlan, wait for the controller-manager deployment.
7. **AutomationOrchestrator CR** — point it at the CNPG service, the two database secrets, the
   derived route host and the generated admin password secret. Wait for `Ready=True`.
8. **Output** — print the route URL, admin user, admin password and PG host.
9. **AAP integration** (only when `aap_gateway_url` is defined) — authenticate to the Orchestrator
   API, change the local admin's email, re-authenticate, configure AAP as an OIDC identity
   provider via `setup_aap_oidc`, create the AAP health-check credential, create the AAP
   integration.

Note that the plan document describes a different ordering for steps 3–6. The order above is what
the playbook actually does and what works: the CNPG operator has to exist before its `Cluster` CR
can be admitted, and the secrets have to exist before the cluster bootstraps.

### Why the admin email gets changed

The Orchestrator CR creates a local `admin` user with email `admin@example.com`. If the AAP admin
has the same email, OIDC login fails with *"This email is already associated with an existing
account"* and SSO is dead. The playbook PATCHes the local admin to
`local-admin@orchestrator.internal` before setting up the identity provider.

Changing the email invalidates the current JWT — the token's email claim no longer matches the
user record, and every subsequent call returns `TOKEN_STALE`. The playbook therefore
re-authenticates immediately afterwards, and that re-auth is **unconditional**:
`ansible.builtin.uri` always reports `changed: false` for a PATCH, so a `when: ... is changed`
guard never fires. Guarding it looks correct on a re-run (where the email is already right) and
breaks every fresh deployment.

---

## Expected pods

After a successful deployment the `automation-orchestrator` namespace contains:

| Pod | Replicas |
|---|---|
| `orchestrator-backend` | 2 |
| `orchestrator-ui` | 2 |
| `orchestrator-worker` | 2 |
| `orchestrator-background-worker` | 1 |
| `orchestrator-redis` | 1 |
| `orchestrator-temporal` | 1 |
| `orchestrator-pg-1` | 1 (CloudNativePG managed) |

The PostgreSQL pod is named `<cluster>-<ordinal>`, so with `cnpg_cluster_name: orchestrator-pg`
and `cnpg_instances: 1` there is exactly one pod, `orchestrator-pg-1`. This is a **single-instance
PostgreSQL with no high availability** — adequate for a demo, not for anything else. Raising
`cnpg_instances` to 3 gives CNPG-managed replication.

The operator's own pod, `automation-orchestrator-operator-controller-manager`, also runs in this
namespace.

---

## Verification

1. The playbook prints the route URL, admin password and PG host.
2. Open the route URL and log in as `admin`.
3. Confirm the **Log in with AAP** button is present — that means the identity provider was
   configured.
4. Log in via AAP SSO.
5. Confirm the AAP integration reports healthy in the Orchestrator UI.
6. Create a test workflow with a Job Execution node pointing at an AAP job template.

---

## Idempotency

- **K8s secrets** — check-before-create. Passwords are generated on the first run only; on a
  re-run the existing values are read back out of the secrets, so the CR and the database stay
  consistent.
- **Operators** — OLM OperatorGroups, Subscriptions and InstallPlan approval are all idempotent.
- **Orchestrator objects** — identity provider, credential and integration are each preceded by a
  list call and skipped if a match already exists.
- **Admin email PATCH** — skipped when the email is already correct, but the re-auth that follows
  it runs unconditionally (see above).

---

## Cleanup and teardown

### AAP side — required between deployments

`cleanup-aap-orchestrator.yml` lists OAuth2 applications on the AAP gateway
(`/api/gateway/v1/applications/`), selects any whose name matches `syntara` or `orchestrator`, and
deletes them.

This must run before deploying Orchestrator against a new OCP cluster. `setup_aap_oidc` refuses to
create a duplicate OAuth2 application, so a leftover app from a destroyed cluster makes every
subsequent deployment fail. Give it its own AAP job template.

### OCP side — not automated, by design

There is no teardown playbook for the OCP resources. In this demo the cluster itself is disposable:
when a deployment is finished with, the whole demo.redhat.com cluster is destroyed and a new one is
requested. Removing the namespace, CRs and operators individually would be wasted effort. The only
thing that survives a cluster teardown is the AAP-side OAuth2 application, which is exactly what
the cleanup playbook exists to remove.

---

## Files

| File | Purpose |
|---|---|
| `deploy-automation-orchestrator.yml` | Main playbook — operators, PostgreSQL, CR, AAP integration |
| `cleanup-aap-orchestrator.yml` | Removes Orchestrator OAuth2 apps from AAP (run between clusters) |
| `sync-collections.yml` | **Not used in the current deployment.** Sets a requirements filter on the `rh-certified` remote in private automation hub, triggers a sync, and waits for it. Kept for the day the EE switches back to `ee-minimal-rhel9` and collections have to come from PAH at runtime instead of being baked into the image. |
| `vars/main.yml` | Non-secret defaults |
| `vars/vault.yml` | Encrypted AAP credentials (committed encrypted) |
| `vars/vault.yml.example` | Template — copy, fill, `ansible-vault encrypt` |
| `collections/requirements.yml` | Not read at runtime. The collection list for the planned `ee-minimal-rhel9` switch, see below |
| `execution-environment/execution-environment.yml` | EE base image reference |
| `PLAN.md` | Original design plan (historical) |
| `PROMPT.md` | The implementation prompt used to build this |
| `documentation.md` | This file |

### What `collections/requirements.yml` is for

It is **not** read by AAP project sync. AAP resolves collection requirements relative to the
**project root**, at `<project>/collections/requirements.yml` — not relative to the playbook's
directory. This repo's project root is `AAP-advanced-features/`, so a file at
`automation-orchestrator/collections/requirements.yml` is never read by project sync, regardless
of content. Today the collections come baked into the `ee-supported-rhel9` image and nothing
installs from this file at all.

Its job is to be the input for the planned switch to a minimal custom EE. It is already wired into
`execution-environment/execution-environment.yml` as `dependencies.galaxy`, commented out
alongside the `ee-minimal-rhel9` base image:

```yaml
# dependencies:
#     galaxy: ../collections/requirements.yml
```

`ansible-builder` resolves that path relative to the definition file, so it reaches the sibling
`collections/` directory and lands in the build context as `_build/requirements.yml` (verified with
ansible-builder 3.1.1). The same file is what `sync-collections.yml` would push to private
automation hub if collections ever have to be pulled at runtime instead.

So the switch is a two-line uncomment rather than a rebuild of the EE definition — which is why
the file is kept, and why the list in it has to stay accurate even though nothing reads it today.

---

## Running it

### As an AAP job template (the intended path)

1. **Project** — point at this repo, enable `scm_update_on_launch` so the playbook is always current.
2. **Execution environment** — `ee-supported-rhel9:latest`, pull policy `Never`, image already in
   AAP's podman storage.
3. **Inventory** — one `localhost` host with `ansible_connection: local`. The play also sets
   `connection: local`, so any inventory works.
4. **Job template** — project + EE + inventory + `deploy-automation-orchestrator.yml`.
5. **Vault credential** — attach one so `vars/vault.yml` decrypts at runtime.
6. **Survey** — `ocp_api_url` (text) and `ocp_admin_password` (password). Add `ocp_admin_username`
   (text, default `admin`) if the cluster uses a different admin account.

No custom credential types are needed: AAP secrets live in the vault file, OCP credentials come
from the survey.

### From the CLI

```bash
ansible-playbook deploy-automation-orchestrator.yml --ask-vault-pass \
    -e ocp_api_url=https://api.cluster.example.com:6443 \
    -e ocp_admin_password=<password>
```

Requires `redhat.openshift`, `kubernetes.core` and the `kubernetes` Python library locally.

---

## Key decisions (as built)

1. **Pure Ansible, no `oc` CLI, no shell, no manual UI steps.** The playbook has to be runnable as
   an AAP job template.
2. **`redhat.openshift.k8s` for mutations, `kubernetes.core.k8s_info` for queries.**
   `redhat.openshift` has no `k8s_info` module.
3. **Raw OLM rather than `aapctl`.** Red Hat's documented install path is the `aapctl` CLI, which
   would violate the no-CLI constraint. Installing the Subscriptions directly produces the same
   operators.
4. **Stock `ee-supported-rhel9`, no custom EE build.** AAP 2.7 gateway authentication prevents
   `ansible-galaxy` from pulling collections from PAH during project sync, so the collections have
   to be in the image. `ee-supported-rhel9` (~2.5GB) already has them. The plan originally called
   for a custom `ee-minimal-rhel9` build (~500MB) plus a PAH sync; that was abandoned.
5. **CloudNativePG for PostgreSQL** rather than an external database. Demo-appropriate only — Red
   Hat does not support CNPG.
6. **`setup_aap_oidc` rather than manual OAuth app creation.** The plan originally called for
   creating an OAuth application and a limited service account on AAP via `ansible.platform` and
   handing Orchestrator only a client ID and secret. The Orchestrator API turned out to expose a
   single endpoint that does the whole thing, so AAP admin credentials are passed once,
   transiently, and are not stored by Orchestrator. `ansible.platform` ended up unused.
7. **AAP admin credentials *are* stored** in Orchestrator's own credential store (encrypted at
   rest) for the integration health check. This is the one place credentials persist outside the
   vault.
8. **No secrets in git in plaintext.** AAP credentials are vault-encrypted and committed; OCP
   credentials come from the survey; the OCP token is obtained at runtime; PostgreSQL and
   Orchestrator admin passwords are generated at runtime and live only in K8s secrets.
9. **No separate subscription manifest.** The Orchestrator operator is published in the
   `redhat-operators` catalog, which the cluster's own pull secret already covers, and the AAP
   subscription includes the Orchestrator entitlement. Nothing extra has to be uploaded.
10. **S3 file storage skipped.** With `spec.fileStorage` omitted, file uploads return HTTP 503.
    Not needed for the demo.
11. **LLM provider integration deferred.**

---

## Orchestrator REST API reference (reverse-engineered)

The Orchestrator REST API is underdocumented — the official guide shows UI field labels, not JSON
bodies. The API is Pydantic v2 with `extra="forbid"`, so any unknown field is rejected outright.
These schemas were recovered by reading the source inside the backend pod, at
`/opt/app-root/src/src/syntara/`.

### Authentication

```
POST /api/v1/auth/login
Body:     {"username": "admin", "password": "..."}
Response: {"access_token": "..."}
```

All later requests need `Authorization: Bearer <token>`.

### Current user

```
GET /api/v1/users/me
Response: 200 — the authenticated user, including id and email
```

Used to find the local admin's UUID before patching the email.

### Update a user

```
PATCH /api/v1/users/{id}
Body:     {"email": "local-admin@orchestrator.internal"}
Response: 200
```

Changing the email invalidates the caller's own JWT (`TOKEN_STALE`). Re-authenticate immediately.

### Identity provider — automatic AAP setup

```
POST /api/v1/identity_providers/setup_aap_oidc
Body:
  aap_url: string (required)
  organization: string (default "Default")
  admin_username: string (mutually exclusive with personal_access_token)
  admin_password: string (required with admin_username)
  personal_access_token: string (alternative to username/password)
  insecure_skip_tls_verify: bool (default false)
Response: 201 — IdentityProviderRead
```

Creates the OAuth2 application on AAP and configures the OIDC identity provider in Orchestrator in
one call. Source: `syntara/identity_providers/models/aap_setup.py` → `AAPOIDCSetupRequest`.

Returns `502 AAP_AUTHENTICATION_ERROR` ("AAP authentication failed. Check your admin credentials.")
when the AAP credentials are wrong. That is an Orchestrator-side error, not an AAP API error.

`insecure_skip_tls_verify: true` is required whenever the Orchestrator pods cannot verify AAP's
certificate — self-signed, internal CA, or a reverse-proxy certificate not in the pod trust store.

### Identity provider — manual

```
POST /api/v1/identity_providers
Body:
  name: string (required)
  configuration:
    provider_type: "oidc"
    issuer_url: string
    client_id: string
    client_secret: string
    redirect_uri: string
    idp_type: "aap" | "generic"
    disable_tls_verify: bool
    scopes: string
    auto_discovery: bool
    allow_all_authenticated: bool
    aap_role_mapping_enabled: bool
    enable_rp_initiated_logout: bool
Response: 201
```

### Projects

```
GET /api/v1/projects
Response: {"resources": [...]}
```

Does **not** accept `?search=` — returns `422 Unknown query parameter(s): search`. List everything
and filter client-side. The default project is named `default`.

### Credentials

```
GET  /api/v1/credentials?project_id=<uuid>     # project_id IS accepted here
POST /api/v1/credentials
Body:
  name: string (required)
  credential_type_id: UUID (required)
  project_id: UUID (required)
  inputs: object (required — fields depend on the credential type)
Response: 201
```

Note the asymmetry with `/projects`: the credentials collection does filter server-side on
`project_id`, even though `/projects` rejects `search`. Query-parameter support is per-endpoint —
check before assuming.

Built-in credential types (`GET /api/v1/credential_types`):

| Type | Inputs |
|---|---|
| Ansible Automation Platform | `{username, password}` or `{oauth_token}` |
| LLM Provider | `{api_key}` |
| HTTP Bearer Token | `{token}` |
| HTTP Basic Auth | `{username, password}` |

### Integrations

```
POST /api/v1/integrations
Body:
  name: string (required)
  integration_type: "ansible_automation_platform" | "llm_provider" | "mcp_server" (required)
  management_credential_id: UUID (required for AAP and LLM, optional for MCP)
  configuration:
    integration_type: string (must match the top-level value — discriminator)
    base_url: string
    insecure_skip_tls_verify: bool
    allow_http: bool
    ca_certificate: string | null
  description: string | null
  enabled: bool (default true)
  scope: "global" | "project" (default "global")
  labels: object
  discovered_tools: list (MCP only)
  discovered_models: list (LLM only)
Response: 201
```

Source: `syntara/integrations/models/integration.py` → `IntegrationCreate`.

- Extra fields produce `"Extra inputs are not permitted"`.
- A missing credential produces `INTEGRATION_CREDENTIAL_REQUIRED`.
- The credential field is `management_credential_id` — not `credential_id`,
  `health_check_credential_id` or `connection_credential_id`, all of which the official docs imply.
- `integration_type` must appear **both** at the top level and inside `configuration`.
- The value is `ansible_automation_platform`, not `aap`.

### Conventions

- List responses use `resources` as the array key, not `results`. (The AAP gateway API, by
  contrast, uses `results` — `cleanup-aap-orchestrator.yml` correctly reads `results`.)
- POST bodies nest their settings under `configuration`, even where the docs show them flat.

---

## Known gotchas

**CloudNativePG channel is `stable-v1`.** Using `stable` fails with "no operators found in channel
stable of package cloudnative-pg". The Orchestrator operator itself does use `stable`.

**`kubernetes.core.k8s_info` must be fully qualified.** `redhat.openshift` ships `k8s` but no
`k8s_info`.

**`module_defaults` must use the group, not the FQCN.** `group/kubernetes.core.k8s` is correct;
listing `redhat.openshift.k8s` individually does not work, because `redhat.openshift` modules
redirect to `kubernetes.core` action plugins and `module_defaults` resolves by action plugin group.

**`module_defaults` must be block-level, not play-level.** The OCP API token is created mid-play by
the OAuth task. Play-level `module_defaults` are evaluated before any task runs, so
`api_key: "{{ ocp_api_token }}"` raises an undefined-variable error. Wrap the k8s tasks in a
`block:` and put `module_defaults` on the block.

**The OCP OAuth flow needs `X-CSRF-Token`.** Without the header, OCP 4.21+ answers the
`openshift-challenging-client` request with `401` and
*"A non-empty X-CSRF-Token header is required to receive basic-auth challenges"*. Any non-empty
value works.

**demo.redhat.com clusters use self-signed certificates.** `ocp_validate_certs: false` is required.

**`no_log` is mandatory on credential-bearing tasks.** AAP job events record
`invocation.module_args.body` verbatim, including plaintext passwords. The affected tasks are the
OCP OAuth request and token extraction, Orchestrator login and re-login, `setup_aap_oidc`, and
credential creation. All six use `no_log: "{{ secure_logging | default(true) }}"`.

**Never set a block-level `vars:` default for a variable that may come from the vault.**
`aap_admin_username: "{{ aap_admin_username | default('admin') }}"` is a recursive template loop.
Use `{{ var | default('value') }}` inline in each task instead.

**AAP OAuth2 applications live at `/api/gateway/v1/applications/`.** Not
`/api/controller/v2/applications/` and not `/api/o/applications/` — both 404.

---

## Not automated

- OCP cluster provisioning (requested separately from demo.redhat.com).
- Pulling `ee-supported-rhel9` and loading it into AAP's podman storage.
- LLM provider integration.
- OCP-side teardown — deliberate, see [Cleanup and teardown](#cleanup-and-teardown).

---

## Known gaps / TODO

- [ ] **PostgreSQL version is unpinned and almost certainly unsupported.** Red Hat requires
      PostgreSQL 15, and the official `aapctl` CNPG template pins
      `imageName: ghcr.io/cloudnative-pg/postgresql:15`. The `Cluster` CR in
      `deploy-automation-orchestrator.yml` sets no `imageName`, so CloudNativePG uses whatever its
      operator version defaults to — currently PostgreSQL 17 or newer. Add
      `spec.imageName: ghcr.io/cloudnative-pg/postgresql:15` to the Cluster definition. Note this
      cannot be changed in place on an existing cluster without a database migration.
- [ ] **Switch to `ee-minimal-rhel9`.** `ee-supported-rhel9` is ~2.5GB; a minimal EE with only
      `redhat.openshift` and `kubernetes.core` baked in via `dependencies.galaxy` would be ~500MB.
      Blocked on getting gateway-compatible galaxy credentials working so collections can be
      pulled from PAH. Both `collections/requirements.yml` and `sync-collections.yml` are kept
      ready for this; the EE definition already has the change staged as comments.
- [ ] **Route-host derivation is silent on failure.** A cluster that does not match the
      demo.redhat.com URL convention yields a wrong hostname and an unreachable deployment rather
      than an error. Consider asserting that `ocp_api_url` matches
      `^https://api\.[^:]+:6443$`, and pointing at `orchestrator_route_host` in the failure message.
- [ ] **`cnpg_storage_size` is not exposed as a survey field** — 10Gi is fixed at deploy time and
      resizing later means a PVC expansion.
