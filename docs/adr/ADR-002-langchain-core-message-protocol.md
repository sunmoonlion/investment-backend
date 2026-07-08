# ADR-002: LangChain Core Message Protocol

Date: 2026-07-08
Status: Accepted

## Context

The LLM boundary needs a stable message protocol. The old dict-based message
shape is too loose for replay, schema migration, and validation.

## Decision

Use LangChain Core `BaseMessage` only at the LLM adapter boundary. Persist and
move messages inside the domain as `StoredMessage`.

`StoredMessage` includes:

- `role`
- `content`
- `sequence_no`
- optional `message_id`
- optional `tool_call_id`
- `metadata`
- `schema_version`

Mapping between `BaseMessage` and `StoredMessage` belongs in infrastructure
adapters, not in domain models.

## Consequences

- Domain code must not import `langchain_core`.
- Message persistence always goes through serializers/upcasters.
- LLM provider adapters receive LangChain messages or adapter-produced
  equivalents.
- Message sequence and schema compatibility are testable without live LLM calls.
