# Offer lifetime comparison

> V1 analysis snapshot — historical evidence, not the current operating guide.
> See [current strategy](../automated-trading.md) and [game-day instructions](../game-day.md).

160 deterministic 120-turn simulations: four target offer lifetimes ×
eight conditions × five peer behaviors. This is a controlled model, not
a real-game survival probability.

## Controlled comparison

Only the target offer lifetime changes: 2, 4, 6, or 12 turns, capped by
the server limit. Advertisements stay at two turns, as do peer offers.
Reserve floors, seeking thresholds, payment caps, and gifts are unchanged.
Other planets retain the same modeled policies for every comparison.
Live defaults remain unchanged at two turns.

Conditions and model assumptions are described in [the earlier comparison](policy-comparison.md).
Incoming offers are processed after the scenario delay; expiration occurs first
at the expiry tick. Long offers still reserve their full promised quantities
in the strategy and block another outgoing offer to the same peer.

## Results

| Offer lifetime | Target survived / 40 | Shortage turns while alive | Expired offers | Delivered gift units |
| --- | ---: | ---: | ---: | ---: |
| 2 | 24 / 40 | 176 | 599 | 2689 |
| 4 | 24 / 40 | 176 | 169 | 2708 |
| 6 | 24 / 40 | 176 | 126 | 2756 |
| 12 | 24 / 40 | 176 | 92 | 2741 |

## Survival by response pattern

| Peer behavior | 2 turns | 4 turns | 6 turns | 12 turns |
| --- | ---: | ---: | ---: | ---: |
| responsive | 8 / 8 | 8 / 8 | 8 / 8 | 8 / 8 |
| one_tick_delay | 8 / 8 | 8 / 8 | 8 / 8 | 8 / 8 |
| three_tick_delay | 0 / 8 | 0 / 8 | 0 / 8 | 0 / 8 |
| ignore_P02 | 0 / 8 | 0 / 8 | 0 / 8 | 0 / 8 |
| small_trade_peers | 8 / 8 | 8 / 8 | 8 / 8 | 8 / 8 |

## Paired changes from the two-turn baseline

| Lifetime | Improved from failure to survival | Regressed from survival to failure |
| --- | ---: | ---: |
| 4 | 0 | 0 |
| 6 | 0 | 0 |
| 12 | 0 | 0 |

## Interpretation limits

A longer lifetime allows a delayed acceptance; it cannot force a peer to trade.
A single long-lived offer can also tie up a trading partner while supplies fall.
The three-turn-delay scenario applies to every planet. Peers still issue
two-turn offers, so peer-to-peer exchanges can fail even if target offers last
longer. This deliberately tests a unilateral change, not coordinated adoption.
Stock, health, production, and peer behavior remain modeled assumptions.
Fewer expirations alone do not establish better survival or cooperation.

## Reproduce

```sh
python simulations/compare_offer_lifetimes.py
```

The generated JSON in `run/simulations/` includes per-run policy settings and trajectories.
No live credentials, sockets, or live trades are used.
