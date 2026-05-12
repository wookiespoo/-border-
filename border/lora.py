"""
Phantom LoRa Interface — Last Mile for the Truly Disconnected
Broadcast internet content via radio to people with NO internet access.

Hardware required for a Bridge node:
  - Raspberry Pi 3/4/Zero (~$35)
  - LoRa HAT module SX1276/SX1278 (~$15)
  - Solar panel + battery pack (~$30)
  - Weatherproof enclosure (~$10)
  Total: ~$90 per bridge node

Hardware required for a Receiver:
  - ESP32 microcontroller (~$5)
  - LoRa module SX1276 (~$8)
  - Small OLED display (~$3) — optional
  - Battery + solar (~$15)
  Total: ~$30 per receiver (down to ~$10 without display/solar)

Deploy bridge nodes near censored borders with free internet on one side.
Receivers inside censored areas can receive content up to 15km away.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import math
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, Generator, List, Optional

logger = logging.getLogger("phantom.lora")

# LoRa packet constraints
LORA_MAX_PAYLOAD = 222   # bytes (255 max - headers - FEC)
LORA_HEADER_SIZE = 8     # bytes
LORA_DATA_SIZE = LORA_MAX_PAYLOAD - LORA_HEADER_SIZE  # 214 bytes per packet

# Content priorities for broadcast queue
PRIORITY_EMERGENCY = 0   # Emergency alerts — always first
PRIORITY_NEWS = 1        # News articles
PRIORITY_MEDICAL = 2     # Medical information
PRIORITY_WIKIPEDIA = 3   # Wikipedia pages
PRIORITY_GENERAL = 4     # General cached pages


@dataclass
class LoRaPacket:
    """
    A single LoRa radio packet (max 255 bytes).
    Large content is chunked across multiple packets.

    Wire format (8 byte header + up to 214 bytes data):
    | 1 byte  | 2 bytes    | 2 bytes  | 1 byte  | 2 bytes  | up to 214 bytes |
    | version | session_id | seq_num  | total   | checksum | payload_chunk   |
    """
    version: int = 1
    session_id: int = 0       # 16-bit session identifier
    seq_num: int = 0          # packet sequence within session
    total_packets: int = 1    # total packets in this session
    payload_chunk: bytes = b""

    def to_bytes(self) -> bytes:
        checksum = sum(self.payload_chunk) & 0xFFFF
        header = struct.pack(
            ">BHHBH",
            self.version,
            self.session_id,
            self.seq_num,
            self.total_packets,
            checksum,
        )
        return header + self.payload_chunk

    @classmethod
    def from_bytes(cls, data: bytes) -> "LoRaPacket":
        version, session_id, seq_num, total_packets, checksum = struct.unpack(">BHHBH", data[:8])
        payload_chunk = data[8:]
        return cls(
            version=version,
            session_id=session_id,
            seq_num=seq_num,
            total_packets=total_packets,
            payload_chunk=payload_chunk,
        )


@dataclass
class LoRaContent:
    """Content item queued for LoRa broadcast."""
    content_id: str
    content_type: str       # "news", "medical", "wikipedia", "page", "emergency"
    title: str
    body: str
    url: Optional[str] = None
    language: str = "en"
    priority: int = PRIORITY_GENERAL
    queued_at: float = field(default_factory=time.time)

    def to_compressed_bytes(self) -> bytes:
        """Serialize and compress content for radio transmission."""
        data = {
            "id": self.content_id,
            "type": self.content_type,
            "title": self.title,
            "body": self.body[:4000],  # cap at 4KB for radio
            "url": self.url,
            "lang": self.language,
            "ts": int(self.queued_at),
        }
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        return gzip.compress(raw, compresslevel=9)

    @classmethod
    def from_compressed_bytes(cls, data: bytes) -> "LoRaContent":
        raw = gzip.decompress(data)
        d = json.loads(raw)
        return cls(
            content_id=d["id"],
            content_type=d["type"],
            title=d["title"],
            body=d["body"],
            url=d.get("url"),
            language=d.get("lang", "en"),
            queued_at=d.get("ts", time.time()),
        )


class LoRaChunker:
    """Splits content into LoRa-sized packets and reassembles them."""

    @staticmethod
    def chunk(content: LoRaContent, session_id: int) -> List[LoRaPacket]:
        """Split content into LoRa packets."""
        compressed = content.to_compressed_bytes()
        chunks = [
            compressed[i:i + LORA_DATA_SIZE]
            for i in range(0, len(compressed), LORA_DATA_SIZE)
        ]
        total = len(chunks)

        packets = []
        for seq, chunk in enumerate(chunks):
            packets.append(LoRaPacket(
                version=1,
                session_id=session_id,
                seq_num=seq,
                total_packets=total,
                payload_chunk=chunk,
            ))

        logger.debug(f"[LoRa] Chunked '{content.title[:30]}' → {total} packets "
                     f"({len(compressed)} bytes compressed)")
        return packets

    @staticmethod
    def reassemble(packets: List[LoRaPacket]) -> Optional[LoRaContent]:
        """Reassemble packets back into content."""
        if not packets:
            return None

        total = packets[0].total_packets
        if len(packets) != total:
            logger.warning(f"[LoRa] Incomplete: have {len(packets)}/{total} packets")
            return None

        # Sort by sequence number
        packets.sort(key=lambda p: p.seq_num)
        compressed = b"".join(p.payload_chunk for p in packets)

        try:
            return LoRaContent.from_compressed_bytes(compressed)
        except Exception as e:
            logger.error(f"[LoRa] Reassembly failed: {e}")
            return None


class LoRaBroadcaster:
    """
    Bridge node broadcaster — sends internet content via LoRa radio.
    Runs on a Raspberry Pi with LoRa HAT attached.

    In simulation mode (no hardware), logs what would be broadcast.
    With hardware, uses the RPi.GPIO + SX127x library to transmit.
    """

    def __init__(
        self,
        frequency_mhz: float = 868.0,
        simulation_mode: bool = True,
    ):
        self.frequency_mhz = frequency_mhz
        self.simulation_mode = simulation_mode
        self._queue: List[LoRaContent] = []
        self._session_counter = 0
        self._packets_sent = 0
        self._lora = None

        if not simulation_mode:
            self._init_hardware()

    def _init_hardware(self) -> None:
        """Initialize LoRa hardware (Raspberry Pi + SX127x HAT)."""
        try:
            # Requires: pip install RPi.GPIO spidev pyLoRa
            from SX127x.LoRa import LoRa
            from SX127x.board_config import BOARD
            BOARD.setup()
            self._lora = LoRa(verbose=False)
            self._lora.set_freq(self.frequency_mhz)
            self._lora.set_spreading_factor(10)    # SF10 = longer range, slower
            self._lora.set_bw(125e3)               # 125kHz bandwidth
            self._lora.set_coding_rate(5)          # 4/5 coding rate
            logger.info(f"[LoRa] Hardware initialized at {self.frequency_mhz}MHz")
        except ImportError:
            logger.error("[LoRa] Hardware libraries not found. Run: pip install RPi.GPIO spidev pyLoRa")
            self.simulation_mode = True
        except Exception as e:
            logger.error(f"[LoRa] Hardware init failed: {e}")
            self.simulation_mode = True

    def queue(self, content: LoRaContent) -> None:
        """Add content to the broadcast queue."""
        self._queue.append(content)
        self._queue.sort(key=lambda c: c.priority)
        logger.info(f"[LoRa] Queued: '{content.title[:40]}' (priority={content.priority})")

    def queue_news(self, title: str, body: str, url: str = "") -> None:
        """Queue a news article for broadcast."""
        self.queue(LoRaContent(
            content_id=hashlib.sha256(title.encode()).hexdigest()[:8],
            content_type="news",
            title=title,
            body=body,
            url=url,
            priority=PRIORITY_NEWS,
        ))

    def queue_emergency(self, message: str) -> None:
        """Queue an emergency alert — always broadcast first."""
        self.queue(LoRaContent(
            content_id=f"emg_{int(time.time())}",
            content_type="emergency",
            title="EMERGENCY ALERT",
            body=message,
            priority=PRIORITY_EMERGENCY,
        ))

    async def broadcast_next(self) -> Optional[LoRaContent]:
        """Broadcast the next item in the queue."""
        if not self._queue:
            return None

        content = self._queue.pop(0)
        session_id = self._session_counter % 65536
        self._session_counter += 1

        packets = LoRaChunker.chunk(content, session_id)

        if self.simulation_mode:
            compressed_size = len(content.to_compressed_bytes())
            logger.info(
                f"[LoRa SIM] Broadcasting '{content.title[:40]}'\n"
                f"           {len(packets)} packets × {LORA_MAX_PAYLOAD} bytes\n"
                f"           Compressed: {compressed_size} bytes\n"
                f"           Frequency: {self.frequency_mhz}MHz\n"
                f"           Range: ~10-15km urban, ~50km line-of-sight"
            )
            self._packets_sent += len(packets)
        else:
            await self._transmit_packets(packets)

        return content

    async def _transmit_packets(self, packets: List[LoRaPacket]) -> None:
        """Actually transmit packets via hardware."""
        import asyncio
        for packet in packets:
            raw = packet.to_bytes()
            if self._lora:
                self._lora.write_payload(list(raw))
                self._lora.set_mode_tx()
            await asyncio.sleep(0.1)  # ~100ms between packets
        self._packets_sent += len(packets)

    @property
    def queue_size(self) -> int:
        return len(self._queue)

    @property
    def stats(self) -> dict:
        return {
            "frequency_mhz": self.frequency_mhz,
            "queue_size": self.queue_size,
            "packets_sent": self._packets_sent,
            "simulation_mode": self.simulation_mode,
        }


class LoRaReceiver:
    """
    Receiver — runs on an ESP32 or Raspberry Pi with LoRa module.
    Listens for Phantom broadcasts and assembles content.
    This is the $10-$40 device deployed inside censored regions.
    """

    def __init__(self, simulation_mode: bool = True):
        self.simulation_mode = simulation_mode
        self._sessions: Dict[int, List[LoRaPacket]] = {}
        self._received_content: List[LoRaContent] = []

    def receive_packet(self, raw: bytes) -> Optional[LoRaContent]:
        """Process a received LoRa packet. Returns content if complete."""
        try:
            packet = LoRaPacket.from_bytes(raw)
        except Exception as e:
            logger.warning(f"[LoRa Receiver] Bad packet: {e}")
            return None

        session_id = packet.session_id
        if session_id not in self._sessions:
            self._sessions[session_id] = []

        self._sessions[session_id].append(packet)

        # Check if we have all packets for this session
        if len(self._sessions[session_id]) >= packet.total_packets:
            content = LoRaChunker.reassemble(self._sessions[session_id])
            if content:
                self._received_content.append(content)
                del self._sessions[session_id]
                logger.info(f"[LoRa Receiver] ✓ Received: '{content.title[:40]}'")
                return content

        return None

    @property
    def received(self) -> List[LoRaContent]:
        return list(self._received_content)
