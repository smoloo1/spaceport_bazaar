# Client Action Log

Clients should be able to produce a readable log based on each completed live simulation. It should be able to be generated using a script that takes in the structured log over every action, and produce a summary.

## Summary Structure

The summary should list every action in order, and provide some way to filter by type.

There should be a visual identification on which resource is the planet's specialty.

There should be an overall count of advertisements sent and average uptime, count of offers accepted by other planets out of all offers sent to other planets (displayed as an unsimplified fraction), a count of accepted offers from other planets out of all offers received from other planets (displayed as an unsimplified fraction), and a "how long survived" metric to the overview detailing which tick the planet ran out of health and disconnected.

### Advertisements
Each advertisement, tick it was sent out, what tick it expired, inventory range while it was active, and offers received during the active duration.

### Sent Offers
Each offer, what planet is was to, and if it was eventually accepted.

Offers should be classified as trades (offering one resource for another) and gifts (offering a resource with nothing in return). 

### Received Offers and Acceptance
A log of every offer received, planet from, and if it was accepted or ignored.

### Connectivity
Flag any downtime or client stalls.

### Inventory and Health
Nice to have - Overview of quantity of each resource in inventory over time and planet health over time presented as a graph

## Implementation
`client/run_live.py` creates a JSONL journal in `run/logs/` when it starts and appends the live simulation events to that file. The report script then reads the journal; it does not connect to or pull directly from the observatory server. It prints a readable report to the terminal and does not create another JSON file.

While `run_live.py` is active, run `python scripts/live-run-log.py --generate-log` in another terminal to report from the newest journal in `run/logs/`. Add `--watch` to refresh the report as new events are written. `--type` can be used with either option, for example `python scripts/live-run-log.py --generate-log --type offer` or `python scripts/live-run-log.py --generate-log --watch --type offer`.

The `--type` argument filters by the action/event type. Common types are `advertise`, `offer`, `accept`, `withdraw`, `ready`, and `sync`; the report may also list `connect`, `disconnect`, `connectivity`, and `decision` when those events occur. The default is `all`. The script prints the available types found in the selected journal. You can also pass a specific journal path, for example `python scripts/live-run-log.py path\to\client-log.jsonl --type offer`.

Excluding **Inventory and Health** as well as **Expansions** section for now. The script has not yet been tested.

> The Implementation section, `scripts/live-run-log.py`, and `client/run-line.py` have been generated or modified using Codex according to this specification document.

## Expansions 
An interactive HTML site to track past logs and graphs to compare performance across runs. As well as the same log functionality for local test servers (once a testing server is available).
