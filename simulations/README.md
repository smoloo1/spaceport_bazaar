# Offline strategy experiments

These are optional research tools, not client startup commands. They use the
preserved class log at `data/run-2-log.json` as scenario input and
make explicit assumptions about peer behavior. They do not reproduce human
teams' decisions or establish real-game survival probabilities.

Run from the repository root:

```sh
python simulations/compare_p02.py
python simulations/compare_offer_lifetimes.py
```

`compare_p02.py` retains its existing name for command compatibility. It varies
starting stocks, upkeep, production, and specialties; it does not assume future
P02 games match the class run. `compare_offer_lifetimes.py` changes only our
offer lifetime while keeping peer policies and advertisement lifetimes fixed.

Both write JSON trajectories and Markdown reports under the Git-ignored
`run/simulations/` directory. `--log` selects another compatible class export;
`--output` changes the output JSON path. The Markdown report uses the same stem.
They may take a minute to finish and need no sockets or live credentials.

## V1 findings retained for reference

- [Class-game analysis](../docs/analysis/class-game-analysis.md)
- [Seeking thresholds and trade sizes](../docs/analysis/policy-comparison.md)
- [Offer lifetimes](../docs/analysis/offer-lifetimes.md)

These checked-in reports are historical snapshots, labeled V1. Generated raw
trajectories are local output; rerunning an experiment does not overwrite the
historical reports. Promote a new report deliberately after reviewing it.
For what the client currently does, read the
[current strategy guide](../docs/automated-trading.md).
