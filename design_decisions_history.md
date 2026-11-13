# Design decisions history

This retrospective uses the repository's Git history and its documentation. Historical
rationale below is documented in those sources; no deployment details or secret values are
recorded here.

## Select the intended AAP explicitly for integration and cleanup

Implementation: `e3d32ba`.

The encrypted repository vault selected the original AAP automatically. Restoring those job
templates elsewhere could therefore configure or clean up the original instance. Deployment and
cleanup now accept the intended HTTPS origin and its admin credentials at launch and never read
that vault implicitly. Deployment validates supplied inputs before OpenShift changes; omitting
all AAP inputs still permits standalone deployment. An existing identity provider or integration
pointing elsewhere causes a failure instead of silently retaining the old target.

Cleanup requires exact application names, rejects ambiguous or unexpected lookup results before
deleting applications, and uses only paths built from the explicit target. It follows the AAP 2.7
Gateway authentication flow documented in the local security guide: Basic authentication creates
a temporary Gateway token, Bearer authentication performs cleanup, and an always task revokes the
token after success or failure. Orchestrator application setup keeps its supported admin
username/password API contract. AAP TLS validation defaults to enabled; no platform authentication
settings are changed.

Both full playbooks passed syntax checks in the already cached supported execution environment,
run read-only with networking disabled. Eleven local behavior tests passed using actual Ansible
tasks and a local HTTPS Gateway double with a trusted test certificate. Coverage includes missing
and unsafe inputs, empty standalone survey inputs, target drift, exact name selection, ambiguous
results, missing applications, deletion failures, token revocation, and protected secrets. No live
Orchestrator deployment or AAP application cleanup was run.

## Preserve explicit Yes/No meaning across native survey value types

Implementation: `3f0fc603e0812b5a1d5d8dae07789dab181dc5ef`.

The first native deployment stopped at the initial type guard before any cluster
changes: the AAP job record held Yes, but its runtime value failed the string
test. The guard now accepts only the exact Yes/No strings or genuine boolean
equivalents, with false retaining certificate verification. Numbers, arbitrary
truthy strings and container values remain rejected. Invalid answers report only
their type, and a credential-free message records the selected verification mode.
The template and CaC default remain No.

Eight focused actual Core 2.20 security tests passed, covering both Yes forms,
native false, legacy-flag rejection, malformed values, protected results and CA
cleanup. This is input-handling proof, not completed cluster deployment proof.

## Resolve OpenShift collections through native AAP project requirements

Implementation: `9b64ee1`.

The shared Core 2.20 image supplies the approved Python clients but does not
bundle the OpenShift/Kubernetes Ansible collection group. The project's earlier
stock-image assumption therefore no longer matched the receiving execution
environment. Normal project requirements now request redhat.openshift 6.0.0 and
kubernetes.core 6.6.0 from the receiving organization's existing certified Hub
credentials. Both exact versions and their compatible dependency/Core ranges
were read from that Hub before this change.

The retained builder input has matching pins. No new image, credential, wrapper
or local package installation is introduced. Normal project synchronization and
the actual deployment still need to establish runtime acceptance; Hub metadata
alone is not a successful deployment.

## Require a launch-specific choice before skipping certificate verification

Implementation: `7ae8fb4`.

The owner explicitly chose a required deployment survey named Disable TLS
verification, default No. Only an exact Yes answer for that launch skips certificate
verification for deployment endpoints; HTTPS encryption and protected logging stay
enabled. No or omission verifies certificates. Malformed answers fail before
connections, and older verification variables cannot bypass No. Cleanup retains
its independent verified default. The CaC survey and saved variables must never
persist an opt-out default for later runs.

Eighteen actual Core 2.20 tests passed, including a self-signed HTTPS endpoint that
works only after Yes, default/missing/No rejection, malformed answer rejection and
continued masking of synthetic credentials. This changes the explicitly chosen
deployment policy; it does not establish a completed OpenShift deployment.

## Protect deployment results and verify certificate chains

Implementation: `d108d584826b9dfc13be001ae3bb58ec5b34deef`.

Preparation for a new explicitly requested deployment exposed a generated administrator
password in the summary and unprotected registered token facts. The deployment block now
inherits secure logging, and the summary points to the existing Kubernetes Secret without
printing its value. The job template, credential sources and deployment architecture stay
the same.

OpenShift and Orchestrator certificate verification now default to enabled, matching AAP.
A trusted cluster CA can be supplied through protected launch variables and passed to the
native Kubernetes modules using a temporary file, removed on success or failure. This
supports privately signed API certificates without accepting an unverified certificate or
disabling TLS checks.

Fifteen actual Ansible tests passed under Core 2.20 with synthetic credentials and local
HTTPS services. They include rejection of an untrusted server before sending credentials,
masking token facts and authenticated responses, a password-free summary, and CA-file
cleanup after failure. The existing HTTPS test server was corrected to consume and check
POST bodies before replying; early replies caused broken pipes with Core 2.20. These are
offline results, not proof of deployment to a cluster. The generic cached test image lacks
the Kubernetes collection group needed for a full playbook syntax check; that check did
not establish success in that image. No new dependencies were installed for verification.

## Deploy through Ansible and reuse the supported execution environment

Implementation: `d949bf0`.

The initial standalone repository contains the OpenShift deployment, optional AAP integration,
and AAP cleanup playbooks. The documented choices were to use certified OpenShift modules
instead of an `oc` command workflow, install the operators through OLM, and use the supported
execution environment with its existing collections. CloudNativePG provides a disposable demo
database; the documentation explicitly distinguishes this from a supported production database.
Orchestrator's `setup_aap_oidc` API handles AAP application registration, preserving that API's
supported username/password contract.

## Keep the collection list outside the project-sync requirements path

Implementation: `eac17c5`; comment correction: `8a686d7`.

The collection requirements moved under `execution-environment/` because the configured Galaxy
source could not supply them during project sync. The existing supported image already contained
the required collections, so installing them again at sync time was unnecessary. The builder
configuration retains the list for a possible future custom image. The following commit corrected
the explanation of how the builder resolves its relative path; it changed no behavior.

## Record validation after splitting out the repository

Documentation: `5d41b68`.

The repository records a successful end-to-end run after the split, including Vault loading,
operators, database, Orchestrator, and AAP integration. This is historical validation of the old
implicit-target implementation. It does not validate later explicit-target changes or a new
environment.
