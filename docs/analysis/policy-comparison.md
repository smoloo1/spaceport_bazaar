# Strategy comparison: varied starting conditions

> V1 analysis snapshot — historical evidence, not the current operating guide.
> See [current strategy](../automated-trading.md) and [game-day instructions](../game-day.md).

200 deterministic 120-tick simulations: five policies × eight starting/production
conditions × five peer behaviors. These are constructed scenarios, not estimates
of real-game survival probability or a replay of classmates' decisions.

## Compared policies

- Inactive: no actions, including no acceptance of gifts.
- Current: seek imported resources below 5 upkeep ticks; pay at most 2 units.
- Earlier only: seek below 10 ticks; pay at most 2 units.
- Larger only: seek below 5 ticks; pay at most 5 units.
- Both: seek below 10 ticks; pay at most 5 units.

All active variants keep the same spending floors: 3 upkeep ticks for the
production specialty, 5 for other resources. Live defaults have not changed.

## Results

| Policy | Target survived / 40 | Sum of shortage ticks while alive | Delivered gift units |
| --- | ---: | ---: | ---: |
| inactive | 0 / 40 | 440 | 0 |
| current | 24 / 40 | 176 | 2689 |
| earlier_only | 24 / 40 | 176 | 2664 |
| larger_only | 24 / 40 | 176 | 2689 |
| earlier_and_larger | 24 / 40 | 176 | 2599 |

The shortage count includes the tick of failure but excludes subsequent ticks.
Gift units measure delivered resources, not proof that those gifts saved a peer.

## Conditions and peer behavior

The recorded run supplies initial values, upkeep, production sequences, and public
rules. The target is labeled P02 for continuity; its specialty is not hardcoded
to water. Eight conditions are tested:

1. Recorded start: (30, 30, 30).
2. Low start: target inventory (8, 6, 6).
3. Uneven start: target inventory (40, 2, 18).
4. Abundant start: target inventory (50, 50, 50).
5. Higher upkeep: target consumes (1, 2, 1) per tick.
6. Production outage: target produces nothing during ticks 15–35 inclusive.
7. Food specialty: rotate the whole world's resource labels once.
8. Components specialty: rotate them twice.

Five peer behaviors: immediate responses; one-tick delay; three-tick delay;
no offers involving the target accepted; and immediate peers restricted to
two-unit payments. Other peer profiles permit five-unit payments. Peers use
the current strategy with five-tick seeking and remain unchanged across target
policy comparisons. They have finite inventories, production, upkeep, and health.

## Interpretation

- All four active variants survived all eight conditions under immediate,
  one-tick-delay, and small-trade-peer profiles, with no shortage ticks there.
- All failed when nobody traded with the target or when all responses took
  three ticks. Every variant currently expires offers after two ticks.
- Earlier seeking and larger trades did not improve survival in this suite.
  This does not establish that they have no benefit against other opponents.
- The inactive recorded-start case reproduces failure at tick 40 and final
  inventory (450, 0, 0), matching the historical P02 accounting.

Retain current defaults based on this evidence. The next useful experiment
is offer lifetime versus peer response time, followed by intermittent peers
and peers that insist on larger lots. Do not claim an improvement merely
because a proposed parameter sounds plausible.

## Model boundaries

This harness calls the real strategy decision function, not the network
executor. It models production then upkeep, damage/recovery, permanent failure,
offer expiry, finite-stock settlement, per-tick command limits, record budgets,
and sequential decisions after state changes. Trading conserves each resource
and cannot create negative inventory. Scheduling rotates deterministically.

Historical production remains fixed even when our trades change the outcome;
we do not infer whether a different real game would change production.
Bots see only modeled public advertisements, their own inventory, and relevant
offers—not the simulator's future production sequence or private peer stocks.
Response delay is modeled by withholding incoming offers until the chosen tick.
This is not a real server or classroom scheduling model. Full wire validation,
latency within a tick, connection errors, readiness and retries are covered by
separate client tests, not simulated here. Other planets are cooperative bots,
not reconstructions of human teams. Results need more heterogeneous opponents.

## Reproduce

```sh
python simulations/compare_p02.py
```

The generated JSON in `run/simulations/` contains all settings, per-run metrics, and
per-tick target health and inventory. No live credentials or sockets are used.
