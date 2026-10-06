# Client Action Log

After each live game, we want a readable summary of everything our client did. A script should read the client's structured log of every action and turn it into that summary.

## What the Summary Should Show

The summary lists every action in the order it happened, and can be filtered to one action type.

The planet's specialty resource is marked (★) wherever resources appear.

### Overview
- **Advertisements:** how many we sent, and how many ticks they stayed active on average.
- **Our offers:** how many other planets accepted out of how many we sent, e.g. `3/8`. Fractions are not simplified, so `4/8` stays `4/8` instead of becoming `1/2`.
- **Offers to us:** how many we accepted out of how many other planets sent us, e.g. `2/5`.
- **Survival:** how many ticks the planet lasted, and the tick where its health ran out.

### Advertisements
For each advertisement: the tick we sent it, the tick it expired, the lowest and highest amount of each resource in our inventory while it was active, and how many offers we received while it was active.

### Sent Offers
For each offer: which planet we sent it to, and whether they accepted it.

Each offer is labeled as a **trade** (we give a resource and ask for one back) or a **gift** (we give a resource and ask for nothing).

### Received Offers
For each offer another planet sent us: who sent it, what it asked for, and whether we accepted it or ignored it.

### Connectivity
Flag any time the client was disconnected, missed ticks, or stalled.

### Inventory and Health
Graphs of each resource in our inventory, and of planet health, over the course of the game.

## Implementation
When `client/run_live.py` starts, it creates a new log file (a JSONL journal) in `run/logs/` and writes every game event to it as the game runs. `scripts/live-run-log.py` reads those files and shows them in the terminal. It never connects to the game server and doesn't write any files. It needs nothing beyond the Python standard library.

### Usage
Open a second terminal (before or after starting `run_live.py`) and run:

```sh
python scripts/live-run-log.py
```

This opens a live dashboard that follows the newest game in `run/logs/` and updates as the client writes. If the client reconnects, the dashboard combines every log file from the same game.

| Key | Does |
| --- | --- |
| `1`–`5` or ←/→ | Switch tab: Overview, Timeline, Offers, Ads, Connection |
| ↑/↓, PgUp/PgDn | Scroll |
| `f` | Timeline: cycle the action-type filter |
| `r` | Timeline: show the strategy's reason for each action |
| space | Freeze the view so you can read it (press again to resume) |
| `q` | Quit |

**Overview** is the at-a-glance tab: health (bar and history graph), each resource's stock and how many ticks it lasts, trading totals, and a **Needs attention** list (resource about to run out, health falling, stalled client, offers waiting for you, rejected commands). After the game ends, that list becomes **What happened**: when each resource ran out, the lowest health, unused surplus, and the best trading partner. The planet's specialty is marked ★ everywhere.

Other ways to run it:

| Command | What it does |
| --- | --- |
| `python scripts/live-run-log.py run/logs/<file>.jsonl` | Dashboard for one specific log |
| `python scripts/live-run-log.py --plain` | Print a text report once instead of the dashboard (also happens automatically when output is piped to a file) |
| `python scripts/live-run-log.py --plain --type offer` | Text report listing only `offer` actions |

`--type` accepts `advertise`, `offer`, `accept`, `withdraw`, `ready`, `sync`, `connect`, `disconnect`, `error`, or `advice` (the default is `all`). The report prints which types actually appear in the log. In the dashboard, `--type` sets the Timeline's starting filter.

### Try it with the demo game
`scripts/demo-game.py` runs a small made-up game on your machine (not the real rules) so you can watch the dashboard move. Use three terminals:

```sh
python scripts/demo-game.py                                                    
# 1: the game
BAZAAR_TOKEN=demo python client/run_live.py --url ws://127.0.0.1:8765/ws --automate --ready   
# 2: our client
python scripts/live-run-log.py                                                 
# 3: the dashboard
```

Add `--hard` to the demo game to watch the planet fail. Restart the demo game to start a new game.

Set `NO_COLOR=1` to turn off colors. On consoles that can't show symbols like ★ or █, it switches to plain ASCII.

> Codex wrote the first version of `scripts/live-run-log.py` and edited `client/run_live.py`, based on this spec. Claude fixed its bugs and rebuilt it as the interactive dashboard.