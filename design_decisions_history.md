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
