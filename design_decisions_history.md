# Design decisions history

This retrospective uses the repository's Git history and its documentation. Historical
rationale below is documented in those sources; no deployment details or secret values are
recorded here.

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
