"""Public, game-level async client API.

This module defines the caller-facing surface separately from ``bazaar.py``,
which contains the protobuf transport and wire-level helpers. Game operations
that are not yet implemented remain explicit placeholders.
"""

from typing import Sequence
from uuid import uuid4

import bazaar
import bazaar_pb2 as pb


class Bazaar:
    """Client for one authenticated Bazaar station session.

    The intended lifecycle is::

        async with Bazaar(url, token) as client:
            state = await client.get_state()
            await client.set_ready(state)

    The client owns request IDs, protocol messages, and response handling.
    """

    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token
        self._transport: bazaar.BazaarClient | None = None
        self._state: pb.State | None = None

    async def __aenter__(self) -> "Bazaar":
        if self._transport is not None:
            raise RuntimeError("Bazaar session is already open")
        transport = bazaar.BazaarClient(self.url, self.token)
        await transport.__aenter__()
        self._transport = transport
        self._state = None
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        transport, self._transport = self._transport, None
        self._state = None
        if transport is not None:
            await transport.__aexit__(exc_type, exc, traceback)

    def _require_transport(self) -> bazaar.BazaarClient:
        if self._transport is None:
            raise RuntimeError("Bazaar session is not open; use 'async with Bazaar(...)'")
        return self._transport

    @staticmethod
    def _unproduced_resources(bundle: pb.Bundle, specialty: int) -> list[str]:
        resources = {
            pb.RESOURCE_WATER: "water",
            pb.RESOURCE_FOOD: "food",
            pb.RESOURCE_COMPONENTS: "components",
        }
        return [name for resource, name in resources.items()
                if resource != specialty and getattr(bundle, name) > 0]

    async def _send_trade_command(self, message: pb.ClientMessage,
                                  request_id: str) -> pb.Result:
        """Send a trade command and consume its result and resulting state."""
        transport = self._require_transport()
        await transport.send(message)
        result = None
        while True:
            response = await transport.recv()
            kind = response.WhichOneof("message")
            if kind == "protocol_error":
                error = response.protocol_error
                if bazaar.nullable_value(error.request_id) == request_id:
                    raise bazaar.ProtocolViolation(
                        f"command {request_id} rejected: "
                        f"{pb.ControlCode.Name(error.code)}")
            elif kind == "result" and response.result.request_id == request_id:
                result = response.result
                if not result.ok or result.code != pb.RESULT_CODE_OK:
                    raise RuntimeError(
                        f"command {request_id} failed: "
                        f"{pb.ResultCode.Name(result.code)}")
            elif kind == "state":
                self._state = response.state
                if result is not None:
                    return result

    async def get_state(self) -> pb.State:
        """Return the latest complete game state, requesting one if needed."""
        transport = self._require_transport()
        if self._state is not None:
            return self._state
        while True:
            message = await transport.recv()
            if message.WhichOneof("message") == "state":
                self._state = message.state
                return self._state
            if message.WhichOneof("message") == "protocol_error":
                raise bazaar.ProtocolViolation(
                    f"server protocol error before initial state: "
                    f"{pb.ControlCode.Name(message.protocol_error.code)}")

    async def sync(self) -> pb.State:
        """Request and return a fresh complete game state."""
        transport = self._require_transport()
        state = await self.get_state()
        await transport.send(bazaar.sync(state.run_id))
        while True:
            message = await transport.recv()
            kind = message.WhichOneof("message")
            if kind == "state":
                if message.state.run_id != state.run_id:
                    raise bazaar.ProtocolViolation(
                        "server returned state for a different run")
                self._state = message.state
                return self._state
            if kind == "protocol_error":
                raise bazaar.ProtocolViolation(
                    f"sync rejected: {pb.ControlCode.Name(message.protocol_error.code)}")

    async def set_ready(self, state: pb.State, ready: bool = True) -> None:
        """Declare readiness against the supplied state snapshot."""
        transport = self._require_transport()
        await transport.send(bazaar.ready(
            state.run_id, state.snapshot_sequence, is_ready=ready))
        while True:
            message = await transport.recv()
            kind = message.WhichOneof("message")
            if kind == "readiness":
                ack = message.readiness
                if (ack.run_id != state.run_id
                        or ack.snapshot_sequence != state.snapshot_sequence
                        or ack.ready != ready):
                    raise bazaar.ProtocolViolation(
                        "readiness confirmation does not match the declaration")
                return
            if kind == "protocol_error":
                raise bazaar.ProtocolViolation(
                    f"readiness rejected: "
                    f"{pb.ControlCode.Name(message.protocol_error.code)}")

    async def advertise(
        self,
        selling: Sequence[int],
        seeking: Sequence[int],
        expires_tick: int,
    ) -> str:
        """Publish interests, only listing produced resources as for sale."""
        state = await self.get_state()
        invalid = sorted(set(selling) - {
            pb.RESOURCE_WATER, pb.RESOURCE_FOOD, pb.RESOURCE_COMPONENTS,
        })
        if invalid:
            raise ValueError(f"unsupported resource values in selling: {invalid}")
        invalid = sorted(set(seeking) - {
            pb.RESOURCE_WATER, pb.RESOURCE_FOOD, pb.RESOURCE_COMPONENTS,
        })
        if invalid:
            raise ValueError(f"unsupported resource values in seeking: {invalid}")
        unproduced = [
            pb.Resource.Name(resource)
            for resource in set(selling)
            if resource != state.self.specialty
        ]
        if unproduced:
            raise ValueError(
                "cannot advertise resources this station does not produce "
                "as for sale: " + ", ".join(sorted(unproduced)))
        request_id = str(uuid4())
        result = await self._send_trade_command(
            bazaar.advertise(
                state.run_id, request_id, selling, seeking, expires_tick),
            request_id,
        )
        object_id = bazaar.nullable_value(result.object_id)
        if not object_id:
            raise bazaar.ProtocolViolation(
                "successful advertisement result has no advertisement ID")
        return object_id

    async def offer(
        self,
        recipient_id: str,
        give: pb.Bundle,
        receive: pb.Bundle,
        expires_tick: int,
    ) -> str:
        """Make an offer and return its created offer ID."""
        state = await self.get_state()
        unproduced = self._unproduced_resources(give, state.self.specialty)
        if unproduced:
            raise ValueError(
                "cannot offer resources this station does not produce: "
                + ", ".join(unproduced))
        request_id = str(uuid4())
        result = await self._send_trade_command(
            bazaar.offer(state.run_id, request_id, recipient_id,
                         give, receive, expires_tick),
            request_id,
        )
        object_id = bazaar.nullable_value(result.object_id)
        if not object_id:
            raise bazaar.ProtocolViolation("successful offer result has no offer ID")
        return object_id

    async def accept(self, offer_id: str) -> None:
        """Accept an incoming open offer if its payment is produced here."""
        state = await self.sync()
        matching = [offer for offer in state.offers.items
                    if offer.offer_id == offer_id
                    and offer.recipient_id == state.self_station_id
                    and offer.status == pb.OFFER_STATUS_OPEN]
        if not matching:
            raise ValueError(f"no open offer {offer_id!r} addressed to this station")
        unproduced = self._unproduced_resources(
            matching[0].receive, state.self.specialty)
        if unproduced:
            raise ValueError(
                "cannot accept an offer that requires resources this station "
                "does not produce: " + ", ".join(unproduced))
        request_id = str(uuid4())
        await self._send_trade_command(
            bazaar.accept(state.run_id, request_id, offer_id), request_id)

    async def withdraw(self, object_id: str) -> None:
        """Withdraw an advertisement or other withdrawable object."""
        state = await self.get_state()
        request_id = str(uuid4())
        await self._send_trade_command(
            bazaar.withdraw(state.run_id, request_id, object_id), request_id)
