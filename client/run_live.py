"""Connect to the live Bazaar game and display incoming updates."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from google.protobuf.message import DecodeError
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

import bazaar
import bazaar_pb2 as pb


def load_live_token(env_file):
    # Only read the client token; the observatory token has a separate purpose.
    token = os.environ.get("BAZAAR_TOKEN")
    if token is None:
        token = dotenv_values(env_file, interpolate=False).get("BAZAAR_TOKEN")
    if not token or not token.strip():
        raise ValueError("Set BAZAAR_TOKEN in the project .env file or environment.")
    return token.strip()


async def observe(client, declare_ready=False):
    pending_ready = None
    first_state = True
    while True:
        # Live games can be idle between updates; don't impose the exercise timeout.
        msg = await client.recv(timeout=None)
        kind = msg.WhichOneof("message")
        if kind == "state":
            state = msg.state
            print(f"Station {state.self_station_id} | {pb.Phase.Name(state.phase)} | "
                  f"{len(state.offers.items)} offers | "
                  f"{len(state.advertisements.items)} advertisements | "
                  f"{len(state.transactions.items)} transactions", flush=True)
            if first_state:
                first_state = False
                if declare_ready:
                    pending_ready = (state.run_id, state.snapshot_sequence)
                    await client.send(bazaar.ready(*pending_ready))
                else:
                    print("Observing. Restart with --ready to declare readiness.", flush=True)
        elif kind == "readiness":
            ack = msg.readiness
            if pending_ready is not None:
                if (ack.run_id, ack.snapshot_sequence) != pending_ready or not ack.ready:
                    raise bazaar.ProtocolViolation("Server did not confirm the readiness declaration.")
                pending_ready = None
                print("Readiness confirmed. Listening for game updates.", flush=True)
        elif kind == "protocol_error":
            if msg.protocol_error.close_session:
                raise bazaar.ProtocolViolation("Server requested that this session close.")
            if pending_ready is not None:
                raise bazaar.ProtocolViolation("Readiness failed; reconnect after resolving the server error.")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="override the live or practice WebSocket URL")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--interactive", action="store_true", help="enable manual trading commands")
    modes.add_argument("--automate", action="store_true", help="automatically trade for survival and cooperation")
    modes.add_argument("--advisory", action="store_true", help="explain strategy recommendations without sending messages")
    parser.add_argument("--reserve-ticks", type=int, default=3, help="upkeep reserve for our production specialty (default: 3)")
    parser.add_argument("--imported-reserve-ticks", type=int, default=5,
                        help="upkeep reserve for resources we do not produce (default: 5)")
    parser.add_argument("--trade-size", type=int, default=2, help="maximum units paid per automated trade (default: 2)")
    parser.add_argument("--gift-size", type=int, default=1, help="maximum units in an outgoing gift; 0 disables gifts")
    parser.add_argument("--emergency-ticks", type=int, default=1,
                        help="allow up to 2:1 when stock covers fewer than this many upkeep ticks (default: 1)")
    parser.add_argument("--practice", action="store_true",
                        help="use the local practice server and its P01 credentials instead of .env")
    parser.add_argument("--credentials", type=Path,
                        help="override the practice credentials file (requires --practice)")
    parser.add_argument("--env-file", type=Path,
                        default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--ready", action="store_true",
                        help="declare readiness after receiving the first state")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="display full decoded messages")
    args = parser.parse_args()
    if args.credentials and not args.practice:
        parser.error("--credentials requires --practice")
    if args.automate and args.practice:
        parser.error("The supplied practice server requires its fixed script. Use run_exercise.py or the strategy tests to validate locally.")
    if args.advisory and args.ready:
        parser.error("--advisory sends no messages; omit --ready")
    from strategy import Policy
    try:
        policy = Policy(reserve_ticks=args.reserve_ticks, imported_reserve_ticks=args.imported_reserve_ticks,
                        trade_size=args.trade_size, gift_size=args.gift_size, emergency_ticks=args.emergency_ticks)
    except ValueError as exc:
        parser.error(str(exc))
    url = args.url or (bazaar.PRACTICE_URL if args.practice else bazaar.DEFAULT_URL)
    try:
        if args.practice:
            credentials = args.credentials or Path(__file__).resolve().parents[1] / "run/validation-credentials.json"
            token = bazaar.load_token(credentials)
        else:
            token = load_live_token(args.env_file)
    except (ValueError, OSError, LookupError) as exc:
        parser.error(str(exc))

    print("Connecting to Bazaar. Press Ctrl+C to stop.", flush=True)
    try:
        async with bazaar.BazaarClient(url, token, verbose=args.verbose) as client:
            if args.automate or args.advisory:
                from automated import run_automated
                await run_automated(client, policy, advisory=args.advisory)
            elif args.interactive:
                from manual import interact
                await interact(client, args.ready)
            else:
                await observe(client, args.ready)
    except InvalidStatus as exc:
        status = exc.response.status_code
        if status == 401:
            hint = "Authentication rejected. Check your assigned client token (not the observatory token)."
        elif status == 403:
            hint = "Access denied by the server or its proxy. Check station access with the game administrator."
        elif status == 400:
            hint = "The server rejected the request. Check station access and support for bazaar.protobuf.v2."
        elif status == 404:
            hint = "WebSocket endpoint not found. Confirm the /ws URL with the game administrator."
        elif status >= 500:
            hint = "Server or gateway error. Ask the game administrator to check the endpoint and upstream server; this does not establish that your token is invalid."
        else:
            hint = "The endpoint did not accept the WebSocket upgrade. Check its configuration with the game administrator."
        print(f"Connection rejected: HTTP {status}. {hint}", file=sys.stderr)
        return 1
    except InvalidHandshake:
        print("WebSocket handshake failed. Check the endpoint and support for bazaar.protobuf.v2.", file=sys.stderr)
        return 1
    except ConnectionClosed:
        print("Connection closed. Restart the client to reconnect.", file=sys.stderr)
        return 1
    except (OSError, TimeoutError):
        print("Connection failed. Check the network and server availability.", file=sys.stderr)
        return 1
    except (bazaar.ProtocolViolation, DecodeError):
        print("Protocol error. Review the last server message before reconnecting.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nDisconnected.")
