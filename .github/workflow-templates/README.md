# Workflow templates (starter workflows)

Thin starter workflows that consumer repositories can copy verbatim to wire
themselves up to the reusable workflows in this repo.

> **Note on discovery.** GitHub's "Suggested workflows" UI only auto-populates
> from `<org>/.github/workflow-templates/`. These templates live in
> `blackoutsecure/platform-automation` (next to the reusable workflows they
> call) so they stay in sync with the workflow contracts they target — at the
> cost of no longer appearing in the org-wide picker. Copy the desired file
> into a consumer repo's `.github/workflows/` and commit it.
>
> If you want the picker UX back, mirror these files into
> `blackoutsecure/.github/workflow-templates/` (e.g. via a sync workflow).

## Available templates

| File | Purpose | Reusable workflow it calls |
|------|---------|----------------------------|
| [docker-build-push.yml](docker-build-push.yml) | Multi-arch Docker build, push to Docker Hub, sync description. | `.github/workflows/docker-build-push.yml` |
| [balena-block-publish.yml](balena-block-publish.yml) | Resolve a block version, optionally sync `balena.yml`, publish to balenaCloud. | `.github/workflows/balena-block-publish.yml` |
| [monitor-upstream-release.yml](monitor-upstream-release.yml) | Poll an upstream repo's `latest` release on a schedule and dispatch downstream workflows. | `.github/workflows/monitor-upstream-release.yml` |
| [release.yml](release.yml) | Tag-driven end-to-end release: Docker → Balena → GitHub Release. | `.github/workflows/release.yml` (**meta-workflow**) |

The `release.yml` template is intentionally tiny — the actual orchestration
lives in the [release meta-workflow](../workflows/release.yml), so adding,
removing, or reordering stages is a single change in one place.

## How to use a template

1. Copy the desired `*.yml` into the consumer repo at `.github/workflows/`.
2. Replace the `$default-branch` placeholder if your editor hasn't already
   (GitHub does this automatically when starter workflows are picked from the
   UI; manual copies need a manual edit).
3. Set the required `vars` and `secrets` listed in the template header.
4. Commit and push.

## `*.properties.json` sidecars

Each template ships with a sibling `<name>.properties.json` describing the
title, description, icon, categories, and file-pattern hints. These are read
by GitHub's starter-workflow picker. They are kept here for parity with the
templates themselves so any future move back into `<org>/.github/` is a
straight copy.
