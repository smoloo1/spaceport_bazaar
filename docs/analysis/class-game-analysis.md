# Class game analysis

> V1 analysis snapshot — historical evidence, not the current operating guide.
> See [current strategy](../automated-trading.md) and [game-day instructions](../game-day.md).

Source: [class game log](../../data/run-2-log.json). The internal
run ID is `run-40`. This is an observational analysis of a historical game,
not a replay proving how our current bot would perform. No strategy code was
changed for this analysis.

## Outcome

Nine planets played 120 ticks. The configured tick interval was 5 seconds,
so the scheduled simulation duration was 10 minutes, excluding any pauses.
Seven planets failed; two survived. The recorded collective success flag is
false. A shortage tick means at least one upkeep resource was missing; counts
include ticks after permanent failure.

| Planet | Produced resource (inferred from totals) | Final health | Failure tick | Shortage ticks | Completed trades involving planet |
| --- | --- | ---: | --- | ---: | ---: |
| P01 Nova | components | 0 | 83 | 57 | 53 |
| P02 Pelagos | water | 0 | 40 | 90 | 0 |
| P03 Miranda | food | 0 | 55 | 85 | 11 |
| P04 Altair | components | 0 | 60 | 72 | 105 |
| P05 Vega | water | 0 | 63 | 76 | 21 |
| P06 Nacre | food | 100 | Survived | 0 | 93 |
| P07 Cinder | food | 0 | 81 | 49 | 67 |
| P08 Eos | water | 25 | Survived | 15 | 259 |
| P09 Carina | components | 0 | 103 | 37 | 55 |

Trade participation counts include gifts and count each settlement for both
participants. Unique completed settlements total 332: 270 exchanges and 62 gifts.

## What the data establishes

- All nine planets started with 30 water, 30 food, and 30 components, and used
  one of each per tick. Different future runs may use different starting stocks.
- Every planet produced only one resource in this log. Recorded per-tick
  production varied among 1, 2, 4, 5, and 6 units. Each planet's total production
  was 540 over 120 ticks. These totals include production after failure; they
  do not establish what an active planet could always trade or guarantee future output.
- At tick 31, before the first failure, world inventories totaled 391 water,
  417 food, and 402 components, while Pelagos had already begun missing upkeep.
  Supplies existed somewhere but were not reaching every planet.
- Pelagos completed zero trades, first missed upkeep at tick 31, and failed
  at tick 40. It ended with 450 water and no food or components.
- Nacre completed 93 trades, imported exactly 90 water and 90 components,
  exported neither, and never missed upkeep. Initial 30 plus 90 imports covered
  its 120 units of consumption for each imported resource. It ended with zero
  of those resources but full health: zero stock after the last upkeep is not
  the same as missing that upkeep.
- Eos survived with 25 health after 15 shortage ticks and participated in 259
  trades. High trade volume alone did not eliminate its shortages.
- Altair sent 114 components as gifts but failed at tick 60. It also exported
  12 food and 19 water despite producing neither. This supports checking imported
  resource reserves; it does not prove its component gifts caused its failure.
- Final world stocks were 1,110 water, 1,111 food, and 1,061 components. Much was
  held by failed planets whose trading was disabled, so it was not all available
  to rescue survivors at the end.

## Command and offer behavior

Of 635 offers, 332 were accepted, 263 expired, and 40 were withdrawn.
Of 1,477 recorded request results, 1,158 were OK and 319 were rejected:
185 RATE_LIMITED, 65 STATION_FAILED, 28 NOT_OPEN, 25 INSUFFICIENT_RESOURCES,
9 EXPIRED, and 7 INVALID_ARGUMENT.

The configured limit was 10 new commands per station per tick. Our executor's
budgets, snapshot updates, phase/failure checks, and cooldowns address these
classes of failures, but the historical log does not prove it would avoid every
rejection. The exact causes of INVALID_ARGUMENT need request-level investigation.

64 of the 270 non-gift settlements had more than two units on at least one
side. Thirty-five were 3-for-3 exchanges. Our current two-unit payment cap
would decline those 3-for-3 incoming offers even when comfortably affordable.
A larger reserve-bounded cap deserves testing; accepting every large offer
would not be justified.

## Implications for our strategy

1. **Protect imported resources.** Our different reserve targets and specialty-only
   gifts address a real risk illustrated here. For uninterrupted full-game
   supply in this configuration, each planet needs at least 90 net imports of
   each non-produced resource, regardless of its large specialty stock.
2. **Seek partners earlier.** Five imported-resource reserve ticks equal just
   25 seconds in this run. Waiting until stock falls below five may leave little
   time for slow or unresponsive peers. Test an earlier seeking threshold or
   larger target, separate from the minimum stock we refuse to spend.
3. **Consider larger affordable exchanges.** Test 3–5 unit trades against the
   existing two-unit cap while retaining reserves and commitment accounting.
4. **Do not rely on a single trading partner.** Eos participated in 259 of 332
   settlements (about 78%). That concentration is a possible fragility, not
   proof that it caused failures.
5. **Measure cooperation by delivery and survival.** A posted gift is not yet
   aid received. Track accepted gifts and subsequent supply coverage. Do not
   infer private peer inventory from advertisements in the live bot.
6. **Treat emergency premiums as a fallback.** Paying 2-for-1 cannot help if no
   peer responds. Earlier recurring exchange is worth comparing against late
   emergency action.

## Next assessment

Use these observed starting stocks, upkeep, production patterns, and failure
rules to design longer simulations. Compare our bot with a no-trading baseline
and with alternative reserve horizons and trade sizes. Include peers that
ignore offers, respond slowly, or stop trading after failure. Historical
production is a useful scenario input, not future knowledge available to a bot.

Measure survival, health, shortage ticks before failure, imports of each needed
resource, accepted help sent, expired offers, and rejected commands. A replay
can show what our policy would recommend at a recorded snapshot; it cannot tell
us what other players would have done in response to different offers.

You identified your team as P02 (Pelagos) and confirmed you could not participate
because the client was not set up. Its zero-trade result is therefore an inactive
baseline, not a failure of our current automated strategy. The log does not
establish which code other teams ran.

For P02 specifically: food and components lasted through tick 30. From tick 31,
it missed two upkeep units per tick. With five damage per missing unit and
100 initial health, ten such ticks reduced health to zero at tick 40. This is
consistent with both the recorded rules and its first failure tick. Future
local scenarios can compare our active policy with this same no-trading baseline.
