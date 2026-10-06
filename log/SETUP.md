# Live Game Journal Implementation Guide

## Purpose

Implement the producer side of a durable event journal for a live game client. The journal should let a separate reader show activity while the game is running and reconstruct what happened afterward.

This guide is intentionally independent of any repository layout, programming language, transport, and event schema. Inspect the target project and adapt the steps below to its conventions. Do not assume that examples or terminology in this guide already exist in the project.

Keep the journal producer and journal reader as separate responsibilities. Preserve the reader's existing input contract when one exists. If the reader contract is missing or insufficient, document a proposed contract before changing either side. Do not modify the reader as part of producer work unless that scope is explicitly authorized.

## Integration profile: this project's `live-run-log.py`

The remainder of this guide is reusable across projects. This section is a concrete adapter profile for the reader in this repository. If implementing a journal for another project, ignore this profile and inspect that project's reader instead. For this project, follow these details exactly so the existing `scripts/live-run-log.py` can consume the journal without changes.

### Discovery and file selection

- The reader's default directory is `<repository-root>/run/logs/`; it searches for files named `client-*.jsonl` and sorts them lexically. Use a UTC timestamp at the start of the filename followed by a unique suffix, for example `client-20261006T184500-a1b2c3d4.jsonl`.
- Write a distinct file per client process/session. A reconnect is a new process/session and therefore a new file.
- The reader identifies a game by finding the first received state in each file and reading `message.state.run_id`. It follows the newest game and combines later files with that same run ID; later files with no state yet may be included as pending reconnect attempts. Do not put a made-up run ID on connection events.
- When given a file path explicitly, the reader reads that file. Its default discovery still points at `run/logs/`, so the producer's default must match that location.
- The live reader can encounter a partially written final line. Write each record as one JSON object plus a newline and flush after each record.

### Required record envelope and encoding

Every line must be a JSON object with these top-level fields:

```json
{"schema_version":1,"sequence":1,"timestamp":"2026-10-06T22:45:00+00:00","event":"session_start"}
```

Use `schema_version: 1`, an integer `sequence` starting at 1 and increasing within the file, an ISO 8601 UTC `timestamp`, and a string `event`. Additional event-specific fields are top-level siblings of these envelope fields. Use the protocol's canonical JSON field names for message payloads (in this project's Protobuf implementation, serialize with `preserving_proto_field_name=True`). Protobuf 64-bit integer fields may appear as decimal strings; the reader handles that representation.

### Event names and fields consumed by the reader

| `event` | Fields / behavior required by `live-run-log.py` |
| --- | --- |
| `session_start` | Optional lifecycle marker; reader marks session active. |
| `session_end` | Lifecycle marker; reader uses it to determine that the run/session ended. |
| `connected` | Marks connectivity active; include `subprotocol` for diagnostics. |
| `disconnected` | Marks connectivity inactive; include optional `sent`, `received`, and `error_type`. |
| `connection_error` | Include `error_type` and optional `http_status`; reader shows a connection problem. |
| `message` | Required for game contents. Include `direction` equal to `sent` or `received` and `message` as the JSON object for one complete protocol message. |
| `decision` | Automated/advisory reasoning. Include `tick`, `reason`, `action`, `arguments`, and `request_id` as available. A decision with an action and request ID is linked to the sent command with that request ID; an action without a matching command is shown as advice. |

The `message` payload must be a single protocol message object keyed by its message kind. The reader depends on these payloads:

- A **received** state is `{"state": {"run_id": ..., "tick": ..., ...}}`. It supplies game identity, inventory, health, station names, rules, offers, advertisements, transactions, and tick history.
- A **sent** command is keyed by its command type, such as `advertise`, `offer`, `accept`, `withdraw`, `ready`, or `sync`. The nested command body and `request_id` are used to show actions and correlate results.
- A **received** `result` includes `request_id`, `ok`, and result code fields so the reader can display command outcomes.
- A **received** `readiness` or `protocol_error` is used to resolve readiness and rejected commands.

The reader also recognizes `policy` as an optional event for metadata, but does not need it to reconstruct actions. It tolerates unknown event names; do not rely on that as a substitute for the required events above. Do not emit a second, competing representation of a command result: preserve the raw received message event.

### Mode coverage and lifecycle

Create the journal before attempting the network connection, then record `session_start`. Route the same journal into the shared connection/client layer so all successfully sent and decoded received protocol messages are recorded in observe, manual, automated, and advisory modes. Record `connected` after protocol negotiation succeeds; record `connection_error` for failures during connection setup, even when no connected event exists. Record `disconnected` when the connection closes and `session_end` during final cleanup, including on failed connection attempts and user interruption when the process can write it.

For automated/advisory modes, record `policy` settings once and a `decision` for each considered action/reason. Record the decision immediately before the command is sent so the reader can associate it by `request_id`. Do not duplicate messages at mode-specific call sites if the shared client layer already logs them.

### Compatibility constraints

- Do not change `scripts/live-run-log.py` to make a producer change appear compatible. Adapt the producer to this profile.
- Keep the base envelope and event names stable. Add optional fields compatibly; coordinate any incompatible schema change separately.
- Keep credentials out of all records. The existing reader cannot compensate for secrets written by a producer.
- Keep the default output under `run/logs/`, even if the producer offers an output-directory override. The override is useful for tests and alternate explicit-file reading, but default dashboard discovery only scans the fixed directory.
- Run or adapt the offline journal tests and confirm the reader can load a representative generated file. Do not use real game credentials or a real game server for routine verification.

## 1. Discover the project before editing

Read the project's top-level documentation and any contributor or agent instructions. Find and trace:

- The supported entry point(s) that start a live game session.
- The code that owns the game connection and sends or receives messages.
- The code that handles user commands, automated decisions, and state updates.
- Any existing journal, logger, telemetry, or event reader.
- Tests and sample data covering game sessions or logs.
- Configuration for output paths, ignored/generated files, and secrets.

Follow an actual session from startup through connection, first state, gameplay, disconnection, and shutdown. Identify where events can be captured once for all modes. Avoid duplicate instrumentation in each caller when a shared transport or session layer can capture the same events reliably.

Before editing, inspect the working tree and preserve pre-existing changes. Keep changes focused on the journal producer and its documentation unless the task explicitly includes additional components.

## 2. Define the journal contract

Agree on a small, versioned event format before implementation. The exact names and fields should fit the target system and its reader. At minimum, each record should provide:

- A schema version.
- A per-journal sequence number or another deterministic ordering key.
- A timestamp with an explicit timezone, preferably UTC.
- An event type.
- Event-specific data needed to understand the live session.

Use a line-oriented format such as JSON Lines when practical: one complete record per line. This lets a reader consume a file incrementally while it is being written. Keep event names and field meanings stable; evolve the format compatibly or increment its version when necessary.

The event model should let a reader determine, as applicable:

- When a client session starts, connects, disconnects, reconnects, and ends.
- Which game/run and player/station the session belongs to.
- The authoritative game states received over time.
- The commands or actions sent, including their arguments and correlation identifiers.
- The results, rejections, or protocol errors associated with those commands.
- Automated decisions and their reasons, if the client makes them.
- Connection failures, missed updates, or other conditions needed to diagnose the run.

Capture enough information for the reader to derive its views from the journal alone. Avoid duplicating derived summaries in the event stream unless there is a clear consumer need.

## 3. Storage and lifecycle

Choose a default output location consistent with the target project's conventions. Allow an explicit configuration override when appropriate. Resolve defaults consistently regardless of the caller's working directory.

Create parent directories as needed. Use a unique file per process/session (or another deliberate isolation model) so concurrent sessions cannot corrupt or mix each other's records. Include a sortable timestamp and unique suffix in filenames if that matches project conventions. Never silently overwrite an earlier journal.

Write complete records incrementally and flush them often enough for the live reader's needs. Ensure orderly shutdown closes the file and writes a final session-ended event when possible. Cover normal completion, connection failure, exceptions, and user interruption. If the journal is required for the system's audit/debugging promise, a write failure should be surfaced clearly and handled according to the product's reliability requirements; do not silently imply that an incomplete journal is complete.

Keep generated journals and runtime credentials out of version control. Apply restrictive file permissions when the platform and project requirements call for them.

## 4. Capture events at reliable boundaries

Instrument the narrowest shared boundaries that observe events accurately:

- Record outgoing messages only after the transport accepts/sends them successfully, unless attempted sends are explicitly useful and distinguishable.
- Record incoming messages only after successful decoding/validation, or record decode failures as separate diagnostic events.
- Capture connection lifecycle at the connection owner, including failures that occur before a session context is fully established.
- Capture automated decisions at the decision point, with enough context to link a decision to the action it produced. Record no-action decisions only if they are useful to the reader.
- Let manual and observe modes share transport-level capture for messages they send/receive.

Use correlation IDs and game/run IDs when the protocol provides them. Do not infer acceptance from a sent command; use the authoritative result or subsequent state. Preserve event order where practical and record enough ordering information to resolve ties across reconnects or multiple files.

## 5. Protect credentials and sensitive data

Do not log authorization headers, tokens, cookies, private keys, or secret-bearing connection configuration. Avoid dumping entire environment/configuration objects or transport objects. If sensitive values could appear in nested payloads, apply a deliberate redaction policy before serialization and test it recursively. Prefer allowlisting fields over logging arbitrary objects when payloads may contain secrets.

## 6. Integrate without coupling the reader

Inspect how the existing reader discovers journals, chooses a session, handles partial final lines, orders records, and joins reconnect files. Match that contract where practical. Keep producer output append-only and machine-readable; human-readable dashboards and summaries belong in the reader.

If one game can span multiple client sessions, make sure the journal includes stable run identity and that file naming/order or metadata supports grouping those sessions. Failed reconnect attempts may not have a run identity yet, so define how the reader should associate or exclude them rather than guessing in the producer.

## 7. Verify the implementation

Use offline fixtures and temporary output directories. Do not require a real game server or real credentials for routine verification. Add or adapt focused tests for the guarantees relevant to the project:

- Parent directory creation and configurable/default path behavior.
- Unique files for successive sessions and no overwrite.
- Valid complete records, version metadata, timestamps, and deterministic ordering.
- Incremental visibility before shutdown.
- Coverage of incoming state, outgoing actions, results/errors, and lifecycle events.
- Association of decisions with commands and outcomes where applicable.
- Recursive secret redaction and absence of credentials in the full output.
- Cleanup and finalization on normal and exceptional exits.
- Clear behavior when storage fails.
- Compatibility with the existing reader using representative journal data.

Prefer small unit tests for serialization and lifecycle plus an offline integration check for a full synthetic session. If asked to implement but not test, follow that instruction and state what remains unverified.

## 8. Completion notes

When finishing the implementation, summarize:

- Which runtime boundaries now produce journal events.
- The selected storage location and configuration mechanism.
- The event format/version and any reader assumptions.
- Secret-handling and write-failure behavior.
- The checks performed and any limitations.

Do not claim support for reconnect stitching, live following, or complete outcome reconstruction unless the format and reader have been checked together.
