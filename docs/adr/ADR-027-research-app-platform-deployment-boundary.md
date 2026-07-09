# ADR-027: Research App Platform Deployment Boundary

Date: 2026-07-09
Status: Accepted

## Context

Source code lives in `research-app`, while deploy templates live in the k8s
platform repository. M1 cannot be considered release-ready if only source code is
updated.

## Decision

Keep application implementation in `research-admin-backend` and apply deployment
changes only under the research-app k8s platform path when the runtime requires
them.

The API and Celery worker must run the same backend image with separate runtime
commands and environment wiring. The Node Bull worker is not part of LangGraph
execution.

## Consequences

- Deployment closure is a release-gate phase, not optional cleanup.
- K8s changes must use the existing template/generate/deploy flow.
- Handoff notes must record image tags, commands, cluster validation, and known
  deployment gaps.
- No frontend or user route should enable v4 traffic before the M1 gate passes.
