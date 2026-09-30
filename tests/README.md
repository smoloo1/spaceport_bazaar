# Tests

Run from the repository root after installing dependencies and generating
Protobuf bindings (`sh client/generate.sh`).

```sh
python -m unittest discover -s tests -v
```

This is the fast, offline suite: manual validation, strategy decisions, automated
sequencing, logging, and simulator accounting. `helpers.py` supplies common
synthetic states. It is support code, not another client entry point.

Integration checks require Linux and local socket access:

```sh
python tests/check_automated_local.py
python tests/check_manual_local.py
```

- Automated: temporary simulated WebSocket server, fake token, readiness,
  shortage trade, gift, game phases, and saved decision/result correlation.
- Manual: supplied practice binary in a temporary directory, interactive input,
  and the complete 10-step scripted exchange.

Neither contacts the live game. Passing these checks does not establish live
connectivity or survival with classmates' clients. Longer behavioral experiments
are documented separately in [simulations/README.md](../simulations/README.md).
