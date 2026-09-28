# [how bazaar](https://www.youtube.com/watch?v=C2cMG33mWVY)

![Spaceport Bazaar](artifacts/hero.png)

## Connect to the live game

Install dependencies (or rebuild your devcontainer after dependency changes):

```sh
python -m pip install -r client/requirements.txt
```

Store your assigned client token as `BAZAAR_TOKEN` in the project `.env` file.
Keep your observatory token in `BAZAAR_OBSERVATORY_TOKEN`; the trading client
does not use it. This file is excluded from Git.

```sh
python client/run_live.py
```

This connects to `wss://spaceport.edneo.com/ws`, loads `.env` automatically,
and displays incoming game updates. An exported `BAZAAR_TOKEN` takes precedence
over `.env`. Use `--verbose` to see full offers, advertisements, and state.
Use `--ready` to declare readiness after reading the initial state:

```sh
python client/run_live.py --ready
```

To run the cooperative survival strategy automatically:

```sh
python client/run_live.py --automate
```

This declares readiness, waits for the administrator to start the game, and
selects trades using a three-tick upkeep reserve for our production specialty
and five ticks for other resources. It can offer small gifts of our specialty
from surplus to planets advertising a need. Use `--advisory` instead to display
recommendations without sending commands. See the
[automated trading guide](docs/automated-trading.md) for policy settings,
local tests, and limitations.

Add `--interactive` to inspect the game and send manual trading commands:

```sh
python client/run_live.py --interactive
```

Type `help` for commands, `inventory` for supplies and upkeep, and `ready` to
declare readiness. Trading waits for readiness confirmation and a running game.
See the [manual trading guide](docs/manual-trading.md) for command syntax and
a full local walkthrough. Observation and manual modes do not enable automation.

The client waits through idle periods. Stop with Ctrl+C and
rerun to reconnect. A new connection replaces any existing client connection
for your station. The observatory is at https://spaceport.edneo.com.

## Run the practice exchange

Open this project in its VS Code devcontainer. The container installs Python
dependencies and generates `client/bazaar_pb2.py` automatically.

From the project root, start the practice server:

```sh
sh scripts/start-server.sh
```

In a second terminal in the same container:

```sh
python client/run_exercise.py
```

The runner connects to `ws://127.0.0.1:3001/ws` and reads P01's token from
`run/validation-credentials.json`. It checks the 10-step exchange, then retries
the completed withdrawal with the same request ID to verify cached-result
handling. It also checks P01's strategy against three successive simulated P02
advertisements: a request for P01's water specialty, a food offer P01 can fund
from water surplus, and a request for food while P01 is short on food. The
practice server only supports its fixed exchange, so these last checks evaluate
the strategy's proposed offers against synthetic snapshots. The runner expects
9 sent messages, 18 received messages, and final inventory `(28, 31, 31)`.
The server writes its report to `run/validation-report.json`.

Stop the server with Ctrl+C and restart it before repeating the exercise.
The runner requires a fresh exercise and does not resume an interrupted run.

For a manual Linux setup with Python installed:

```sh
python -m pip install -r client/requirements.txt
sh client/generate.sh
```

Use `--url` and `--credentials` for a different practice server address or
credentials file. Environment tokens never override the credentials file.
To explicitly use an exported token, supply `--token-env BAZAAR_TOKEN` instead
of `--credentials`. The runner does not automatically load `.env`.
The remote URL remains available as `bazaar.DEFAULT_URL` for other clients;
this scripted runner is for servers implementing the starter exercise.

See the [starter guide](artifacts/bazaar-protobuf-starter-linux/README.md)
for protocol details and platform requirements.

## Tests

```sh
python -m unittest discover -s tests -v
python tests/check_manual_local.py
python tests/check_automated_local.py
```

The integration check starts a temporary local practice server and tests the
interactive connection and all 10 exchange steps through the manual controls.
It requires Linux and local socket access; it uses temporary credentials and
reports without contacting the live game.
The automated check uses a separate simulated WebSocket server to exercise
shortages, cooperation, and game phases; the supplied practice server only
accepts its fixed 10-step script.
