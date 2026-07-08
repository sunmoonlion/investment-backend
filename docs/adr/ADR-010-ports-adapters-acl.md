# ADR-010: Ports, Adapters, and ACL Enforcement

Date: 2026-07-08
Status: Accepted

## Context

LangGraph and LangChain are core runtime tools, but they must not become the
shape of the product domain. Once runtime types leak into domain/application
code, later replacement, testing, and schema migration become expensive.

## Decision

Use ports/adapters and anti-corruption-layer tests from M1 onward.

Rules:

- domain agent code must not import `langgraph` or `langchain_core`
- application agent code must not import `langgraph`
- LangGraph/LangChain types live in runtime/infrastructure adapters
- external systems are accessed through ports or adapter-facing services

## Consequences

- Boundary tests are part of the normal test suite.
- New runtime integrations must add adapters rather than importing vendor types
  into domain models.
- The graph runtime can later be extracted or replaced with lower migration
  cost.
