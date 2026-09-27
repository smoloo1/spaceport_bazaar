# Bazaar trading plan

Status: manual controls and the initial cooperative survival strategy are
implemented, with advisory and automated modes. See `docs/manual-trading.md`
and `docs/automated-trading.md` for usage and local validation.

## Objective and open questions

User-confirmed objective: survive while helping the other planets survive.
The formal game score remains unconfirmed; the initial policy optimizes neither
a known individual score nor a known collective score.

Confirm with the game administrator:

- Is success individual, collective, or both? How is it scored?
- How does production change over time, and is future production guaranteed?
- Are there additional trading restrictions or expectations about cooperation?

Read the actual upkeep, health, phase, and command limits from each server
snapshot rather than assuming the practice exercise's values.

## Where the work belongs

| File | Responsibility |
| --- | --- |
| `client/bazaar.py` | Authentication, WebSocket framing, Protobuf decoding, and command builders. |
| `client/manual.py` | Manual inspection and trading commands; initial implementation exists. |
| `client/strategy.py` | Pure decision logic: snapshot to recommendation, without network calls. |
| `client/automated.py` | Readiness, result matching, command budgets, cooldowns, and automated execution. |
| `client/run_live.py` | Connection lifecycle and selection of observation, manual, advisory, or automated modes. |
| `client/run_exercise.py` | Preserve the starter guide's deterministic 10-step protocol check. |
| `tests/` | Command validation, local integration, and future strategy scenarios. |

## Implementation stages

1. Agree on objectives, reserve policy, and allowed trading behavior.
2. Review and finish manual controls: inspect inventory, health, upkeep, rules,
   stations, advertisements, and offers; declare readiness; advertise, offer,
   accept, withdraw, and sync. Document their use.
3. Validate controls against the local practice server and synthetic states.
4. Add advisory mode: explain recommended trades without sending them.
5. Evaluate recommendations across shortages, changing inventory, and other
   players' actions. Adjust reserve targets and exchange rates based on evidence.
6. Add explicit opt-in automation with bounded spending, outstanding offers,
   and command frequency. Verify it in an administrator-run practice game
   when available.

## Proposed initial strategy

### Measure need and surplus

Use the latest complete snapshot as the source of truth. Do not apply
transaction amounts again to inventory that already includes them.

For each resource with positive upkeep:

- Coverage in ticks = current inventory / upkeep per tick.
- Reserve target = upkeep per tick multiplied by a configurable reserve horizon.
- Deficit = max(0, reserve target - inventory).
- Surplus = max(0, inventory - reserve target).

Start evaluation with a three-tick horizon. This is a tunable assumption, not
a game requirement. Zero-upkeep resources have no upkeep deficit; any reason
to retain them beyond trading value depends on the scoring rules.

Initially measure coverage from inventory alone. Report last production
separately; do not assume it guarantees future production.

### Select trades

- Prioritize shortages by lowest coverage, with health informing urgency.
- Advertise surplus resources for resources with deficits. Peer advertisements
  express interest; they do not prove available stock.
- Recommend incoming offers that improve the most urgent shortage while
  retaining the configured reserve of resources we pay.
- Interpret incoming offers from the proposer's perspective: we pay `receive`
  and obtain `give`. Compute projected inventory before recommending acceptance.
- Propose small exchanges funded by surplus, with configurable amounts and
  short expirations bounded by the server's TTL limits.
- Count potential outgoing commitments when proposing additional offers:
  offers do not reserve stock on the server, so individually affordable offers
  can collectively overcommit our surplus.
- Reassess or withdraw stale outgoing offers after inventory or needs change.

Initial implementation uses provisional one-for-one outgoing exchanges of at
most two units and gifts of at most one unit per peer per tick, funded by
uncommitted surplus. Trade and gift caps are configurable. It does not relax
reserves for emergencies. These choices need evaluation; they are not claims
that one-for-one is fair or gifting surplus is always beneficial.

### Control execution

- Read state and confirm readiness on every connection before trading.
- Send trades only while the game is running and the station remains eligible.
- Match results to request IDs and use snapshots to confirm the resulting state.
- Use unique IDs for new commands. Retry an uncertain command only with its
  original ID and unchanged payload; never blindly repeat it with a new ID.
- Respect rate, request-record, open-offer, payload-size, and expiration limits.
- Honor `retry_after_tick` and `close_session`; avoid automatic retry loops.
- Reevaluate after updates rather than replaying the practice sequence.
- Reset session assumptions on reconnect and discard old run-specific state
  when the run changes.

## Validation and acceptance criteria

The existing local exercise verifies authentication, readiness, message
encoding, advertisements, offers, acceptance, withdrawal, errors, and sync.
It stays at tick zero and cannot demonstrate long-term strategy performance.

Before enabling automated trades, test:

- Waiting for readiness and for the administrator to start or resume the game.
- Shortages, zero upkeep, surplus, gifts, and unfavorable incoming offers.
- A trade relieving one shortage while creating another.
- Competing outgoing offers and inventory changes between recommendation and execution.
- Expired offers, rejection, rate limits, duplicate requests, and disconnects.
- Station failure, finished or aborted games, and a changed run ID.

Advisory recommendations must identify the resource need, projected inventory,
reserve effect, and reason for each action. Automated execution must obey the
same checks and remain an explicit user-selected mode.

Track health, unmet upkeep, shortage ticks, completed trades, rejected commands,
and final outcomes during evaluation. Choose the final optimization target
after the scoring rules are confirmed.
