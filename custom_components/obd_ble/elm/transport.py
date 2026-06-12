"""BLE GATT serial transport for ELM327-style OBD adapters.

The adapter exposes a UART-like service: one characteristic we write commands
to and one that notifies response bytes. Vendors disagree on which UUID is
which (Veepeak documentation and community projects contradict each other on
FFF1/FFF2 direction), so characteristics are selected by their GATT
properties, not by UUID.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection

_LOGGER = logging.getLogger(__name__)

NOTIFY_PROPERTIES = frozenset({"notify", "indicate"})
WRITE_PROPERTIES = frozenset({"write", "write-without-response"})

DEFAULT_READ_TIMEOUT = 5.0


@dataclass(frozen=True)
class AdapterProfile:
    """Known GATT service for a BLE OBD adapter family."""

    name: str
    service_uuid: str


# Searched in order before falling back to scanning every service.
KNOWN_PROFILES: tuple[AdapterProfile, ...] = (
    AdapterProfile(
        name="Veepeak OBDCheck BLE/BLE+ (STN) and common FFF0 adapters",
        service_uuid="0000fff0-0000-1000-8000-00805f9b34fb",
    ),
    AdapterProfile(
        name="LeLink / HM-10 style FFE0 adapters",
        service_uuid="0000ffe0-0000-1000-8000-00805f9b34fb",
    ),
    AdapterProfile(
        name="Vgate iCar Pro BLE",
        service_uuid="e7810a71-73ae-499d-8c15-faa9aef0c3f2",
    ),
)

ADVERTISED_SERVICE_UUIDS: tuple[str, ...] = tuple(p.service_uuid for p in KNOWN_PROFILES)

DeviceFactory = Callable[[], Awaitable[BLEDevice | None]]


class BleTransportError(Exception):
    """Transport-level failure (connect, characteristic discovery, I/O)."""


class BleDisconnectedError(BleTransportError):
    """The adapter dropped the connection mid-exchange."""


class BleSerialTransport:
    """Byte-stream interface over a BLE notify/write characteristic pair."""

    def __init__(self, device_factory: DeviceFactory, name: str = "OBD BLE adapter") -> None:
        self._device_factory = device_factory
        self._name = name
        self._client: BleakClient | None = None
        self._rx_char: BleakGATTCharacteristic | None = None
        self._tx_char: BleakGATTCharacteristic | None = None
        self._buffer = bytearray()
        self._data_event = asyncio.Event()
        self._disconnected = False

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def connect(self) -> None:
        device = await self._device_factory()
        if device is None:
            raise BleTransportError("BLE device not found / not in range")

        self._disconnected = False
        self._buffer.clear()
        self._data_event = asyncio.Event()

        client = await establish_connection(
            BleakClient,
            device,
            self._name,
            disconnected_callback=self._on_disconnect,
        )
        self._client = client

        try:
            self._rx_char, self._tx_char = self._resolve_characteristics(client)
        except BleTransportError:
            await self.disconnect()
            raise

        await client.start_notify(self._rx_char, self._on_notify)
        _LOGGER.debug(
            "Connected to %s: rx=%s tx=%s",
            device.address,
            self._rx_char.uuid,
            self._tx_char.uuid,
        )

    def _on_disconnect(self, _client: BleakClient) -> None:
        self._disconnected = True
        self._data_event.set()

    def _on_notify(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        self._buffer.extend(data)
        self._data_event.set()

    @staticmethod
    def _resolve_characteristics(
        client: BleakClient,
    ) -> tuple[BleakGATTCharacteristic, BleakGATTCharacteristic]:
        """Pick (rx, tx) characteristics by GATT properties.

        Known OBD services are preferred so a generic device with several
        UART-like services does not confuse the picker. The same
        characteristic may serve both directions (FFE1-style adapters).
        """
        services = list(client.services)
        known = {p.service_uuid.lower() for p in KNOWN_PROFILES}
        ordered = sorted(services, key=lambda s: s.uuid.lower() not in known)

        for service in ordered:
            rx = next(
                (c for c in service.characteristics if NOTIFY_PROPERTIES & set(c.properties)),
                None,
            )
            tx = next(
                (c for c in service.characteristics if WRITE_PROPERTIES & set(c.properties)),
                None,
            )
            if rx is not None and tx is not None:
                return rx, tx

        raise BleTransportError(
            "no service with a notify + write characteristic pair; "
            f"saw services: {[s.uuid for s in services]}"
        )

    async def write(self, data: bytes) -> None:
        client = self._client
        if client is None or self._tx_char is None or not client.is_connected:
            raise BleDisconnectedError("not connected")

        use_response = "write-without-response" not in self._tx_char.properties
        try:
            max_len = self._tx_char.max_write_without_response_size if not use_response else 20
        except Exception:
            max_len = 20
        if not max_len or max_len <= 0:
            max_len = 20

        for offset in range(0, len(data), max_len):
            await client.write_gatt_char(
                self._tx_char, data[offset : offset + max_len], response=use_response
            )

    async def read_until(
        self, terminator: bytes = b">", timeout: float = DEFAULT_READ_TIMEOUT
    ) -> bytes:
        """Read until ``terminator`` is seen; returns bytes including it."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while True:
            index = self._buffer.find(terminator)
            if index != -1:
                end = index + len(terminator)
                chunk = bytes(self._buffer[:end])
                del self._buffer[:end]
                return chunk

            if self._disconnected:
                raise BleDisconnectedError("adapter disconnected while waiting for response")

            remaining = deadline - loop.time()
            if remaining <= 0:
                partial = bytes(self._buffer)
                raise TimeoutError(
                    f"no terminator {terminator!r} within {timeout}s; partial={partial!r}"
                )

            self._data_event.clear()
            try:
                await asyncio.wait_for(self._data_event.wait(), timeout=remaining)
            except TimeoutError:
                continue  # loop re-checks deadline and raises with partial data

    def clear_buffer(self) -> None:
        self._buffer.clear()

    async def disconnect(self) -> None:
        client = self._client
        self._client = None
        self._rx_char = None
        self._tx_char = None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception as err:
            _LOGGER.debug("Error during disconnect: %s", err)
