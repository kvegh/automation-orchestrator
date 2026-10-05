# Automation Orchestrator Setup Plan

> **Historical planning document.** This is the plan as approved in plan mode, before
> implementation. Several decisions in it were superseded while building — notably the custom
> `ee-minimal-rhel9` build, the PAH collection sync, and the manual OAuth app / service account
> path. For how the deployment actually works, see [documentation.md](documentation.md).

## Context

Automation Orchestrator needs to be deployed on a remote RHPDS OpenShift cluster (independent deployment model). AAP 2.7 runs on local VMs (containerized installer). The previous attempt in the AAP-advanced-features repo used a Helm-based approach for the wrong product (Automation Portal) and was deleted. This plan uses the correct OLM operator approach per the 2026.8 documentation.

The goal: a tested, documented, automated setup captured in the `AAP-advanced-features` git repo as a playbook.

---

## Architecture

```
 KVM host
   |
   +-- AAP VM (AAP 2.7 containerized) <--- Automation Gateway on port 443
   |
   +-- managed host VMs
   |
   +-- ...
   
 RHPDS OCP cluster (remote, AWS)
   |
   +-- automation-orchestrator namespace
         +-- Orchestrator Operator (OLM)
         +-- AutomationOrchestrator CR
         +-- PostgreSQL secrets (pointing to external PG or CloudNativePG)
```

Cross-cluster link: Orchestrator --> AAP Gateway on port 443 (HTTPS only, no inbound from AAP to OCP needed).

---

## Decisions (resolved)

1. **PostgreSQL**: CloudNativePG operator on OCP (fine for demo)
2. **Release channel**: `stable`
3. **S3 storage**: Skip for now
4. **LLM provider**: Defer to later
5. **Tooling**: Pure Ansible with `redhat.openshift` certified collection -- no `oc` CLI dependency. Playbook must be runnable from AAP as a job template.
6. **Orchestrator configuration**: No certified Ansible collection exists for Orchestrator's own config (identity providers, integrations). The REST API is the only documented interface -- `ansible.builtin.uri` is the correct approach.
7. **Passwords**: Generated at runtime via `lookup('password', ...)`. Idempotent -- check if K8s secret exists first, only generate if missing. Passwords live only in OCP secrets, never in git.
8. **AAP credentials**: No admin rights handed to Orchestrator. Manual OAuth path -- create dedicated OAuth app and service account on AAP via `ansible.platform`, pass only client_id/secret and service account credentials to Orchestrator.
9. **EE**: Custom build based on `ee-minimal-rhel9` -- add `python3-kubernetes` and `python3-openshift` RPMs only. Collections mounted at runtime via `execution-environment/requirements.yml` (must be synced to PAH).
10. **Disk**: AAP host has sufficient disk space. Custom minimal EE (~500MB) fits comfortably.
11. **Collection sync**: Automated via `ansible.platform` in a pre-flight play running on the default EE. Syncs `redhat.openshift` and `ansible.platform` from console.redhat.com to PAH.
12. **Secrets handling**: Zero secrets in playbook or plan files. OCP token and AAP admin creds injected via AAP credential types as extra vars. PG and Orchestrator admin passwords generated at runtime, stored only in K8s secrets.
13. **Subscription**: No separate manifest needed. Orchestrator operator available via `redhat-operators` catalog (OCP pull secret on RHPDS covers it). AAP subscription includes Orchestrator entitlement.
14. **PostgreSQL**: CloudNativePG operator runs PG pods directly on OCP. No external DB. Fine for demo.
15. **EE build**: Build on the KVM host, not on the AAP VM. Push to PAH container registry. No risk to running AAP.

---

## Constraints (modus operandi)

- **Pure Ansible only.** No `oc` CLI commands. No manual UI steps. No shell scripts.
- **Certified Red Hat collections only:** `redhat.openshift`, `ansible.platform`. No `kubernetes.core` directly.
- **Orchestrator REST API via `ansible.builtin.uri`** for post-deploy configuration (identity provider, integrations) — no certified collection exists for this.
- **No admin credentials handed to Orchestrator.** Manual OAuth path: create OAuth app + service account on AAP via `ansible.platform`, pass only client_id/secret to Orchestrator.
- **No secrets in any file.** Passwords generated at runtime. AAP credentials injected via AAP credential types as extra vars at job launch time.
- **Collections runtime-mounted** from PAH via `execution-environment/requirements.yml`, not baked into EE.
- **EE based on `ee-minimal-rhel9`**, only adds Python libraries. Build on the KVM host, not on the AAP VM.
- **Idempotent.** Re-running the playbook must not break an existing deployment (check-before-create pattern for secrets, operators, CRs).

---

## Steps

### Step 0: Sync collections to PAH (pre-flight, runs on default EE)

Separate playbook or first play — runs on the default EE (which already has `ansible.platform`):

1. Ensure remote registry pointing to `console.redhat.com` exists in PAH
2. Sync `redhat.openshift` collection to PAH
3. Sync `ansible.platform` collection to PAH
4. Verify collections are available

This solves the chicken-and-egg: the default EE has `ansible.platform` built in, so we can use it to sync collections that our custom EE will mount at runtime.

### Step 1: Build and push custom EE (runs on the KVM host, not the AAP VM)

1. `ansible-builder build` on the KVM host using the `execution-environment.yml` from the repo
2. Tag and push the image to PAH's container registry
3. Register the EE in AAP via `ansible.platform`

### Step 2: Prerequisites

- OCP API token provided via AAP credential type (injected as extra var)
- AAP admin credentials provided via AAP credential type (for OAuth app creation only)
- Playbook uses `redhat.openshift.k8s` and `redhat.openshift.k8s_info` -- no `oc` CLI needed
- Authentication via `redhat.openshift.openshift_auth` or API token variable
- Preflight: verify OCP version >= 4.14 and OLM catalog via `k8s_info`

### Step 3: Create namespace and secrets

Create namespace via `redhat.openshift.k8s`.

For each secret: check if it already exists via `k8s_info`. If not, generate a random password with `lookup('password', '/dev/null length=32 chars=ascii_letters,digits')` and create the secret. If it exists, skip (idempotent).

Secrets needed:

```yaml
# orchestrator-pg-credentials
apiVersion: v1
kind: Secret
metadata:
  name: orchestrator-pg-credentials
  namespace: automation-orchestrator
type: Opaque
stringData:
  database: "orchestrator"
  username: "orchestrator_user"
  password: "{{ generated_at_runtime }}"

---
# temporal-pg-credentials
apiVersion: v1
kind: Secret
metadata:
  name: temporal-pg-credentials
  namespace: automation-orchestrator
type: Opaque
stringData:
  database: "temporal"
  username: "temporal_user"
  password: "{{ generated_at_runtime }}"
```

Admin password secret:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: orchestrator-admin-password
  namespace: automation-orchestrator
type: Opaque
stringData:
  password: "{{ generated_at_runtime }}"
```

### Step 4: Install the operator via OLM

```yaml
# OperatorGroup (AllNamespaces scope -- required)
apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata:
  name: automation-orchestrator-operator
  namespace: automation-orchestrator
spec: {}

---
# Subscription
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: automation-orchestrator-operator
  namespace: automation-orchestrator
spec:
  channel: stable  # or early-access
  name: automation-orchestrator-operator
  source: redhat-operators
  sourceNamespace: openshift-marketplace
  installPlanApproval: Manual
```

- Apply via `redhat.openshift.k8s`
- Find and approve InstallPlan via `k8s_info` + `k8s` patch
- Wait for operator CSV to reach Succeeded phase via `k8s_info` with `wait_condition`

### Step 5: Provision PostgreSQL via CloudNativePG (runs on OCP)

- Install CloudNativePG operator (Namespace, OperatorGroup, Subscription from `certified-operators`)
- Wait for CloudNativePG operator ready
- Create Cluster CR with 3 databases (orchestrator, temporal, temporal_visibility)
- Operator auto-generates credential secrets

### Step 6: Create AutomationOrchestrator CR

```yaml
apiVersion: aap.ansible.com/v1alpha1
kind: AutomationOrchestrator
metadata:
  name: orchestrator
  namespace: automation-orchestrator
spec:
  postgres:
    host: "<pg-host>"
    port: 5432
    sslMode: require  # or verify-ca/verify-full
    backendDatabase:
      secretRef:
        name: orchestrator-pg-credentials
    temporalDatabase:
      secretRef:
        name: temporal-pg-credentials
  ingress:
    host: "<orchestrator-route-hostname>"
  secrets:
    initialAdminPasswordSecretRef:
      name: orchestrator-admin-password
```

- Apply via `redhat.openshift.k8s`
- Wait for Ready=True via `k8s_info` with retries
- Retrieve route and admin password via `k8s_info`, display with `debug`

### Step 7: Verify deployment

- Playbook outputs: Orchestrator route URL, admin credentials
- Manual verification: access UI, log in

### Step 8: Create OAuth app and service account on AAP (no admin handover)

Using `ansible.platform` certified collection (not handing admin credentials to Orchestrator):

1. Create a dedicated OAuth2 application on AAP Gateway:
   - Grant type: `authorization-code`
   - Redirect URI: `https://<orchestrator-route>/api/v1/auth/oidc/callback`
   - Record `client_id` and `client_secret`
2. Create a dedicated service account user on AAP for job dispatch (limited permissions, not admin)
3. Assign the service account only the roles needed for launching job templates

### Step 9: Authenticate to Orchestrator API

- Use `ansible.builtin.uri` to POST `/api/v1/auth/login` with admin credentials
- Retrieve JWT access token for subsequent API calls

### Step 10: Add AAP as identity provider (manual OAuth path)

- Use `ansible.builtin.uri` to call the Orchestrator REST API to add AAP as an OIDC identity provider
- Provide: AAP Gateway URL as issuer, `client_id` and `client_secret` from Step 7
- Orchestrator never receives AAP admin credentials

### Step 11: Add AAP integration (for job dispatch)

- Use `ansible.builtin.uri` to call the Orchestrator REST API to create an AAP integration
- Provide: AAP Gateway URL, service account credentials from Step 7 (not admin)
- Test connection via API

---

## Ansible Playbook Design

Target directory: `automation-orchestrator/`

### Files to create

```
automation-orchestrator/
  PLAN.md                               # This plan document
  deploy-automation-orchestrator.yml    # Main playbook (inline k8s definitions, no templates)
  sync-collections.yml                  # Pre-flight: sync collections to PAH (runs on default EE)
  vars/
    main.yml                            # Non-secret variables (namespace, channel, PG config)
    vault.yml.example                   # Template showing required var names (no values)
  execution-environment/
    requirements.yml                    # Runtime collection mounting (redhat.openshift, ansible.platform)
    execution-environment.yml           # EE definition based on ee-minimal-rhel9
  README.md                             # Setup docs
```

No Jinja templates needed -- `redhat.openshift.k8s` takes inline `definition:` dicts directly, which is cleaner and keeps everything in one playbook file.

Passwords for PG and Orchestrator admin are generated at runtime and stored in K8s secrets only -- vault.yml only holds AAP-side credentials needed to create the OAuth app and service account.

### Playbook structure

The playbook runs against `localhost` and uses `redhat.openshift` certified collection for OCP resources, `ansible.platform` for AAP Gateway resources, and `ansible.builtin.uri` for Orchestrator REST API. No `oc` CLI. Authentication via `host` (API URL) + `api_key` (token) variables, or kubeconfig file.

1. **Preflight** -- `k8s_info` to verify OCP version, OLM catalog source
2. **CloudNativePG operator** -- Namespace, OperatorGroup, Subscription, approve InstallPlan, wait for CSV
3. **CloudNativePG Cluster** -- Create Cluster CR with 3 databases on OCP, wait for ready
4. **Orchestrator namespace + secrets** -- Namespace, generate passwords (idempotent), create PG credential secrets and admin password secret
5. **Orchestrator operator** -- OperatorGroup, Subscription, approve InstallPlan, wait for CSV
6. **AutomationOrchestrator CR** -- apply CR, wait for Ready condition
7. **AAP OAuth + service account** -- `ansible.platform` to create OAuth2 app (redirect URI pointing to Orchestrator) and limited-privilege service account on AAP
8. **Configure Orchestrator** -- `uri` to authenticate to Orchestrator API, add AAP as OIDC identity provider (with client_id/secret from step 7), add AAP integration (with service account from step 7)
9. **Output** -- retrieve Route URL and admin password, display

Separate automation (runs before the main playbook, on default EE):
- **Sync collections to PAH** -- ensure `redhat.openshift` and `ansible.platform` are synced from console.redhat.com
- **Build + push custom EE** -- `ansible-builder build` on the KVM host, push to PAH container registry, register in AAP

### Collections needed (runtime-mounted via execution-environment/requirements.yml)

- `redhat.openshift` (certified -- k8s, k8s_info, openshift_auth for OCP resources)
- `ansible.platform` (certified -- OAuth2 app, users, roles on AAP Gateway)
- `ansible.builtin` (uri module for Orchestrator REST API -- built-in, no install needed)

Collections must be synced to Private Automation Hub.

### EE: custom build on ee-minimal-rhel9

```yaml
# execution-environment.yml
version: 3
images:
  base_image:
    name: registry.redhat.io/ansible-automation-platform-27/ee-minimal-rhel9:latest
dependencies:
  system:
    - python3-kubernetes
    - python3-openshift
```

Collections are NOT baked in -- mounted at runtime from PAH. Only the Python libraries that collections depend on are added to the image.

---

## Verification

1. Playbook outputs pod status, CR conditions, route URL, admin password
2. Access Orchestrator UI via route URL
3. Log in as admin
4. Verify "Log in with AAP" button appears (identity provider configured)
5. Log in via AAP SSO -- verify it works
6. Verify AAP integration shows healthy in Orchestrator UI
7. Create a test workflow with a Job Execution node pointing at an AAP job template

---

## What the playbook does NOT automate

- RHPDS OCP cluster provisioning (done separately)
- LLM provider integration (deferred to later)
- Building the custom EE image (separate `ansible-builder build` step, documented in README)
