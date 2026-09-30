"""Bazaar protobuf client: connection, framing, and command builders.

Each WebSocket binary message carries exactly one serialized ClientMessage
(outgoing) or ServerMessage (incoming), with no extra wrapping.
"""

import asyncio
import json

from google.protobuf import text_format
from websockets.asyncio.client import connect

import bazaar_pb2 as pb

PROTOCOL_VERSION = "2.0"
SUBPROTOCOL = "bazaar.protobuf.v2"
MAX_COMMAND_BYTES = 16_384
DEFAULT_URL = "wss://spaceport.edneo.com/ws"
PRACTICE_URL = "ws://127.0.0.1:3001/ws"


class ProtocolViolation(Exception):
    """The server sent something that breaks the wire contract."""


def load_token(credentials_path, station_id="P01"):
    """Read the selected station's token from the specified credentials file."""
    with open(credentials_path) as f:
        creds = json.load(f)
    for player in creds["players"]:
        if player["station_id"] == station_id:
            return player["token"]
    raise LookupError(f"no player with station_id {station_id!r} in {credentials_path}")


# --- Resource helpers -------------------------------------------------------

def bundle(water=0, food=0, components=0):
    return pb.Bundle(water=water, food=food, components=components)


def as_tuple(b):
    """(water, food, components) for a Bundle."""
    return (b.water, b.food, b.components)


def nullable_value(n):
    """The value of a Nullable* wrapper, or None when it holds null."""
    return n.value if n.WhichOneof("kind") == "value" else None


# --- Command builders -------------------------------------------------------
# Every wrapper and required field is set explicitly, including zeros and
# empty lists, because the server rejects messages with absent required fields.

def ready(run_id, snapshot_sequence, is_ready=True):
    msg = pb.ClientMessage()
    msg.ready.type = pb.READY_TYPE_READY
    msg.ready.protocol_version = PROTOCOL_VERSION
    msg.ready.run_id = run_id
    msg.ready.ready = is_ready
    msg.ready.snapshot_sequence = snapshot_sequence
    return msg


def sync(run_id):
    msg = pb.ClientMessage()
    msg.sync.type = pb.SYNC_TYPE_SYNC
    msg.sync.protocol_version = PROTOCOL_VERSION
    msg.sync.run_id = run_id
    return msg


def advertise(run_id, request_id, selling, seeking, expires_tick):
    msg = pb.ClientMessage()
    cmd = msg.advertise
    cmd.type = pb.ADVERTISE_TYPE_ADVERTISE
    cmd.protocol_version = PROTOCOL_VERSION
    cmd.run_id = run_id
    cmd.request_id = request_id
    # SetInParent marks the list wrapper present even when it has no items.
    cmd.body.selling.SetInParent()
    cmd.body.selling.items.extend(selling)
    cmd.body.seeking.SetInParent()
    cmd.body.seeking.items.extend(seeking)
    cmd.body.expires_tick = expires_tick
    return msg


def offer(run_id, request_id, recipient_id, give, receive, expires_tick):
    msg = pb.ClientMessage()
    cmd = msg.offer
    cmd.type = pb.OFFER_COMMAND_TYPE_OFFER
    cmd.protocol_version = PROTOCOL_VERSION
    cmd.run_id = run_id
    cmd.request_id = request_id
    cmd.body.recipient_id = recipient_id
    cmd.body.give.CopyFrom(give)
    cmd.body.receive.CopyFrom(receive)
    cmd.body.expires_tick = expires_tick
    return msg


def accept(run_id, request_id, offer_id):
    msg = pb.ClientMessage()
    cmd = msg.accept
    cmd.type = pb.ACCEPT_TYPE_ACCEPT
    cmd.protocol_version = PROTOCOL_VERSION
    cmd.run_id = run_id
    cmd.request_id = request_id
    cmd.body.offer_id = offer_id
    return msg


def withdraw(run_id, request_id, object_id):
    msg = pb.ClientMessage()
    cmd = msg.withdraw
    cmd.type = pb.WITHDRAW_TYPE_WITHDRAW
    cmd.protocol_version = PROTOCOL_VERSION
    cmd.run_id = run_id
    cmd.request_id = request_id
    cmd.body.object_id = object_id
    return msg


# --- Logging ----------------------------------------------------------------

def describe(msg):
    """One-line summary of a ClientMessage or ServerMessage."""
    kind = msg.WhichOneof("message")
    inner = getattr(msg, kind)
    if kind == "state":
        return (f"state seq={inner.snapshot_sequence} ver={inner.world_version} "
                f"tick={inner.tick} inv={as_tuple(inner.self.inventory)}")
    if kind == "result":
        return (f"result {inner.request_id} ok={inner.ok} "
                f"code={pb.ResultCode.Name(inner.code)} "
                f"object_id={nullable_value(inner.object_id)}")
    if kind == "protocol_error":
        return (f"protocol_error {pb.ControlCode.Name(inner.code)} "
                f"request_id={nullable_value(inner.request_id)} "
                f"close_session={inner.close_session}")
    if kind in ("ready", "readiness"):
        return f"{kind} ready={inner.ready} seq={inner.snapshot_sequence}"
    request_id = getattr(inner, "request_id", "")
    return f"{kind} {request_id}".rstrip()


# --- Connection -------------------------------------------------------------

class BazaarClient:
    """One authenticated WebSocket session.

    Usage:
        async with BazaarClient(url, token) as client:
            msg = await client.recv()
            await client.send(bazaar.sync(run_id))
    """

    def __init__(self, url, token, verbose=False, journal=None):
        self.url = url
        self.token = token
        self.verbose = verbose
        self.sent = 0
        self.received = 0
        self._ws = None
        self.journal = journal

    def record(self, event, **fields):
        if self.journal is not None:
            self.journal.record(event, **fields)

    async def __aenter__(self):
        try:
            self._ws = await connect(
                self.url,
                additional_headers={"Authorization": f"Bearer {self.token}"},
                subprotocols=[SUBPROTOCOL],
            )
        except Exception as exc:
            self.record('connection_error', error_type=type(exc).__name__,
                        http_status=getattr(getattr(exc, 'response', None), 'status_code', None))
            raise
        if self._ws.subprotocol != SUBPROTOCOL:
            await self._ws.close()
            raise ProtocolViolation(
                f"server selected subprotocol {self._ws.subprotocol!r}, expected {SUBPROTOCOL!r}")
        try:
            self.record('connected', subprotocol=SUBPROTOCOL)
        except Exception:
            await self._ws.close()
            raise
        return self

    async def __aexit__(self, *exc):
        await self._ws.close()
        self.record('disconnected', sent=self.sent, received=self.received,
                    error_type=exc[0].__name__ if exc[0] else None)

    async def send(self, msg):
        data = msg.SerializeToString()  # raises EncodeError if a required field is missing
        if len(data) > MAX_COMMAND_BYTES:
            raise ValueError(f"command is {len(data)} bytes; limit is {MAX_COMMAND_BYTES}")
        await self._ws.send(data)  # bytes → binary frame
        self.sent += 1
        self._log("->", msg)

    async def recv(self, timeout=10.0):
        data = await asyncio.wait_for(self._ws.recv(), timeout)
        if isinstance(data, str):
            raise ProtocolViolation(f"expected a binary frame, got text: {data[:200]!r}")
        msg = pb.ServerMessage()
        msg.ParseFromString(data)
        if not msg.IsInitialized() or msg.WhichOneof("message") is None:
            raise ProtocolViolation("server message is missing required fields or a message body")
        self.received += 1
        self._log("<-", msg)
        return msg

    def _log(self, arrow, msg):
        if self.journal is not None:
            self.journal.message('sent' if arrow == '->' else 'received', msg)
        print(f"{arrow} {describe(msg)}")
        if self.verbose:
            print(text_format.MessageToString(msg, indent=4), end="")
