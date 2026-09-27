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

The live entry point currently displays updates and optionally declares readiness;
it does not place trades. It waits through idle periods. Stop with Ctrl+C and
rerun to reconnect. A new connection replaces any existing client connection
for your station. The observatory is at https://spaceport.edneo.com.

## Client API

`client/api.py` defines the async game-level `Bazaar` API. Session management,
reading/syncing state, readiness, advertisements, offers, accepting offers,
and withdrawals are implemented. It only allows this station to give its
specialty resource, as reported by `state.self.specialty`; advertisements also
only list that resource as for sale. The existing `client/bazaar.py` provides
the low-level WebSocket transport and protobuf command builders.

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
`run/validation-credentials.json`. It checks all 10 steps, expecting 8 sent
messages, 16 received messages, and final inventory `(28, 31, 31)`.
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
