# Osoznanie Protocol JSON Schemas

This directory contains the machine-readable contracts for Osoznanie Protocol v0.1.

Each schema is a standalone JSON Schema Draft 2020-12 document:

- `evidence.schema.json`
- `event.schema.json`
- `decision.schema.json`
- `outcome.schema.json`
- `reflection.schema.json`
- `lesson.schema.json`
- `commitment.schema.json`
- `trait.schema.json`
- `identity-snapshot.schema.json`
- `recall-query.schema.json`
- `recall-result.schema.json`

The schemas are generated from the Pydantic protocol and recall models. Do not edit them manually.

## Generate

```bash
python -m osoznanie.schema
```

## Verify

```bash
python -m osoznanie.schema --check
```

The verification command performs an exact comparison and fails when a schema is missing, stale, or unexpected. CI runs this check for every pull request.

## Validate from another language

Any Draft 2020-12 compatible JSON Schema validator can consume these files. A client does not need Python or Pydantic to implement the protocol.

Every schema has:

- a stable `$id` under `https://osoznanie.ai/schemas/v0.1/`;
- `additionalProperties: false` for strict records;
- protocol version metadata in `x-osoznanie-protocol-version`;
- only validation-relevant fields, without generator-specific display titles.

`recall-result.schema.json` constrains `ReasonCode`, provenance object types, score bounds, and the component-only score breakdown.
