# Spaceport Bazaar client

An automated trading client that aims to survive while helping other planets.
**Start with [the game-day guide](docs/game-day.md)** for the next classroom run.

## Setup and run

Open this repository in its VS Code devcontainer. It installs dependencies and
builds the generated Protobuf bindings. For an existing Linux Python environment:

```sh
python -m pip install -r client/requirements.txt
sh client/generate.sh
```

Put your assigned client token in the local, Git-ignored `.env` file:

```dotenv
BAZAAR_TOKEN=your_client_token
```

An exported `BAZAAR_TOKEN` takes precedence over `.env`. The separate observatory
token is not used by this client.

Check the live connection, then stop with Ctrl+C:

```sh
python client/run_live.py
```

A received state confirms connection and decoding. Start automated trading:

```sh
python client/run_live.py --automate
```

This connects to `wss://spaceport.edneo.com/ws`, declares readiness, and waits
for the administrator to start the game. Run only one client for your station.
Run histories are saved under `run/logs/`. See the game-day guide for HTTP
errors, reconnection, and reviewing logs after the game.

## Current documentation

| Read this | For |
| --- | --- |
| [Game day](docs/game-day.md) | Startup, connection checks, logs, and troubleshooting |
| [Architecture](docs/architecture.md) | What each active module does and how they fit together |
| [Automated strategy](docs/automated-trading.md) | Current decision rules, settings, and limitations |
| [Manual controls](docs/manual-trading.md) | Optional interactive debugging and practice walkthrough |
| [Testing](tests/README.md) | Fast checks, integration tests, and how they differ from simulations |

`--advisory` explains recommendations without sending commands. `--interactive`
enables manual controls. These are alternative modes of the same client, not
older versions. The automated strategy is provisional and does not guarantee
individual or collective survival.

## Local checks

```sh
python -m unittest discover -s tests -v
python tests/check_automated_local.py
python tests/check_manual_local.py
```

For the supplied practice exercise, start this in one container terminal:

```sh
sh scripts/start-server.sh
```

Then run this in another terminal in the same container:

```sh
python client/run_exercise.py
```

The exercise uses localhost and generated practice credentials, not your live
credentials. Restart the practice server before repeating it. Its fixed sequence
is a protocol check, not a simulation of our autonomous strategy.

## Repository map

```text
client/           Current runtime code and generated Protobuf bindings
scripts/          Server startup and log summary commands
tests/           Unit tests, local integration checks, and shared fixtures
simulations/      Optional offline strategy experiments
data/  Original class log used by the experiments
docs/             Current guides
  analysis/       Historical V1 analysis snapshots
  archive/v1/     Superseded planning notes
artifacts/        Supplied starter kit and original assets
run/              Local credentials, histories, and generated experiment output (ignored)
```

The original [starter guide](artifacts/bazaar-protobuf-starter-linux/README.md)
is vendor material. Generated `client/bazaar_pb2.py` comes from its schema; do
not edit the generated file by hand.

For previous findings and reproduction commands, see
[the experiments index](simulations/README.md). Historical V1 documents describe
what was planned or tested at that time; current guides above describe what to run.
