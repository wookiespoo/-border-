"""
Tests for border.lora — LoRa packet chunking, broadcasting, receiving
"""
import pytest

from border.lora import (
    LoRaPacket, LoRaContent, LoRaChunker, LoRaBroadcaster, LoRaReceiver,
    LORA_MAX_PAYLOAD, LORA_DATA_SIZE,
    PRIORITY_EMERGENCY, PRIORITY_NEWS, PRIORITY_GENERAL,
)


# ─────────────────────────────────────────────────────────
# LoRaPacket — wire format serialization
# ─────────────────────────────────────────────────────────

class TestLoRaPacket:
    def test_roundtrip(self):
        pkt = LoRaPacket(
            version=1, session_id=42, seq_num=0,
            total_packets=3, payload_chunk=b"hello lora",
        )
        raw = pkt.to_bytes()
        pkt2 = LoRaPacket.from_bytes(raw)
        assert pkt2.session_id == 42
        assert pkt2.seq_num == 0
        assert pkt2.total_packets == 3
        assert pkt2.payload_chunk == b"hello lora"

    def test_header_is_8_bytes(self):
        pkt = LoRaPacket(payload_chunk=b"data")
        raw = pkt.to_bytes()
        assert len(raw) == 8 + len(b"data")

    def test_max_payload_fits(self):
        data = b"x" * LORA_DATA_SIZE
        pkt = LoRaPacket(payload_chunk=data)
        raw = pkt.to_bytes()
        assert len(raw) <= LORA_MAX_PAYLOAD

    def test_version_preserved(self):
        pkt = LoRaPacket(version=2, payload_chunk=b"v2")
        pkt2 = LoRaPacket.from_bytes(pkt.to_bytes())
        assert pkt2.version == 2


# ─────────────────────────────────────────────────────────
# LoRaContent — compression + serialization
# ─────────────────────────────────────────────────────────

class TestLoRaContent:
    def _make_content(self, body_len=500) -> LoRaContent:
        return LoRaContent(
            content_id="news_abc123",
            content_type="news",
            title="Test Article",
            body="A" * body_len,
            url="https://example.com/article",
            language="en",
            priority=PRIORITY_NEWS,
        )

    def test_compress_decompress_roundtrip(self):
        content = self._make_content()
        compressed = content.to_compressed_bytes()
        recovered = LoRaContent.from_compressed_bytes(compressed)
        assert recovered.content_id == content.content_id
        assert recovered.title == content.title
        assert recovered.body == content.body

    def test_compression_reduces_size(self):
        content = self._make_content(body_len=2000)
        compressed = content.to_compressed_bytes()
        raw_len = len(content.body.encode())
        assert len(compressed) < raw_len

    def test_long_body_truncated_to_4kb(self):
        content = self._make_content(body_len=10_000)
        compressed = content.to_compressed_bytes()
        recovered = LoRaContent.from_compressed_bytes(compressed)
        assert len(recovered.body) <= 4000  # capped at 4KB in to_compressed_bytes

    def test_priority_ordering(self):
        assert PRIORITY_EMERGENCY < PRIORITY_NEWS < PRIORITY_GENERAL


# ─────────────────────────────────────────────────────────
# LoRaChunker — split and reassemble
# ─────────────────────────────────────────────────────────

class TestLoRaChunker:
    def _make_content(self, body: str = "Short article body") -> LoRaContent:
        return LoRaContent(
            content_id="test_001",
            content_type="news",
            title="Test",
            body=body,
            priority=PRIORITY_NEWS,
        )

    def test_chunk_produces_packets(self):
        content = self._make_content()
        packets = LoRaChunker.chunk(content, session_id=1)
        assert len(packets) >= 1

    def test_all_packets_same_session(self):
        content = self._make_content()
        packets = LoRaChunker.chunk(content, session_id=7)
        assert all(p.session_id == 7 for p in packets)

    def test_packets_sequential(self):
        content = self._make_content(body="B" * 5000)
        packets = LoRaChunker.chunk(content, session_id=1)
        seq_nums = [p.seq_num for p in packets]
        assert seq_nums == list(range(len(packets)))

    def test_total_packets_field_correct(self):
        content = self._make_content(body="C" * 5000)
        packets = LoRaChunker.chunk(content, session_id=1)
        total = len(packets)
        assert all(p.total_packets == total for p in packets)

    def test_each_packet_fits_lora_max(self):
        content = self._make_content(body="D" * 5000)
        packets = LoRaChunker.chunk(content, session_id=1)
        for pkt in packets:
            assert len(pkt.to_bytes()) <= LORA_MAX_PAYLOAD

    def test_reassemble_recovers_content(self):
        content = self._make_content(body="Full round-trip test! " * 100)
        packets = LoRaChunker.chunk(content, session_id=5)
        recovered = LoRaChunker.reassemble(packets)
        assert recovered is not None
        assert recovered.title == content.title
        assert recovered.body[:100] == content.body[:100]

    def test_reassemble_out_of_order_packets(self):
        content = self._make_content(body="E" * 3000)
        packets = LoRaChunker.chunk(content, session_id=3)
        # Shuffle packets
        shuffled = list(reversed(packets))
        recovered = LoRaChunker.reassemble(shuffled)
        assert recovered is not None
        assert recovered.content_id == content.content_id

    def test_reassemble_incomplete_returns_none(self):
        content = self._make_content(body="F" * 5000)
        packets = LoRaChunker.chunk(content, session_id=2)
        if len(packets) < 2:
            pytest.skip("Need at least 2 packets for this test")
        # Drop last packet
        incomplete = packets[:-1]
        result = LoRaChunker.reassemble(incomplete)
        assert result is None

    def test_reassemble_empty_returns_none(self):
        assert LoRaChunker.reassemble([]) is None


# ─────────────────────────────────────────────────────────
# LoRaBroadcaster — simulation mode
# ─────────────────────────────────────────────────────────

class TestLoRaBroadcaster:
    def test_queue_adds_content(self):
        broadcaster = LoRaBroadcaster(simulation_mode=True)
        content = LoRaContent(
            content_id="b1", content_type="news", title="News",
            body="Body", priority=PRIORITY_NEWS,
        )
        broadcaster.queue(content)
        assert broadcaster.queue_size == 1

    def test_queue_news_helper(self):
        broadcaster = LoRaBroadcaster(simulation_mode=True)
        broadcaster.queue_news("Headline", "Body text", "https://example.com")
        assert broadcaster.queue_size == 1

    def test_emergency_sorts_first(self):
        broadcaster = LoRaBroadcaster(simulation_mode=True)
        broadcaster.queue_news("Regular news", "body")
        broadcaster.queue_emergency("CRITICAL ALERT")
        # Emergency priority is 0 (highest), news is 1
        first = broadcaster._queue[0]
        assert first.content_type == "emergency"

    def test_stats_structure(self):
        broadcaster = LoRaBroadcaster(simulation_mode=True)
        s = broadcaster.stats
        assert "queue_size" in s
        assert "packets_sent" in s
        assert s["simulation_mode"] is True

    @pytest.mark.asyncio
    async def test_broadcast_next_removes_from_queue(self):
        broadcaster = LoRaBroadcaster(simulation_mode=True)
        broadcaster.queue_news("Test", "Test body")
        assert broadcaster.queue_size == 1
        await broadcaster.broadcast_next()
        assert broadcaster.queue_size == 0

    @pytest.mark.asyncio
    async def test_broadcast_empty_queue_returns_none(self):
        broadcaster = LoRaBroadcaster(simulation_mode=True)
        result = await broadcaster.broadcast_next()
        assert result is None

    @pytest.mark.asyncio
    async def test_broadcast_increments_packets_sent(self):
        broadcaster = LoRaBroadcaster(simulation_mode=True)
        broadcaster.queue_news("Test", "Body")
        await broadcaster.broadcast_next()
        assert broadcaster.stats["packets_sent"] > 0


# ─────────────────────────────────────────────────────────
# LoRaReceiver — packet ingestion + reassembly
# ─────────────────────────────────────────────────────────

class TestLoRaReceiver:
    def _make_raw_packets(self, body="Receiver test body " * 50):
        content = LoRaContent(
            content_id="r1", content_type="news",
            title="Receiver Test", body=body, priority=PRIORITY_NEWS,
        )
        packets = LoRaChunker.chunk(content, session_id=99)
        return packets, content

    def test_single_packet_content_received(self):
        receiver = LoRaReceiver(simulation_mode=True)
        packets, content = self._make_raw_packets(body="Short")
        result = None
        for pkt in packets:
            result = receiver.receive_packet(pkt.to_bytes())
        assert result is not None
        assert result.content_id == content.content_id

    def test_multi_packet_reassembled(self):
        receiver = LoRaReceiver(simulation_mode=True)
        packets, content = self._make_raw_packets()
        result = None
        for pkt in packets:
            r = receiver.receive_packet(pkt.to_bytes())
            if r:
                result = r
        assert result is not None
        assert result.title == content.title

    def test_bad_packet_ignored(self):
        receiver = LoRaReceiver(simulation_mode=True)
        result = receiver.receive_packet(b"\xFF\xFF\xFF")  # garbage
        assert result is None

    def test_received_list_grows(self):
        receiver = LoRaReceiver(simulation_mode=True)
        packets, _ = self._make_raw_packets(body="Short")
        for pkt in packets:
            receiver.receive_packet(pkt.to_bytes())
        assert len(receiver.received) == 1

    def test_two_sessions_independent(self):
        receiver = LoRaReceiver(simulation_mode=True)
        c1 = LoRaContent(content_id="c1", content_type="news",
                         title="A", body="body A", priority=PRIORITY_NEWS)
        c2 = LoRaContent(content_id="c2", content_type="news",
                         title="B", body="body B", priority=PRIORITY_NEWS)
        pkts1 = LoRaChunker.chunk(c1, session_id=10)
        pkts2 = LoRaChunker.chunk(c2, session_id=11)
        # Interleave delivery
        all_pkts = pkts1 + pkts2
        for pkt in all_pkts:
            receiver.receive_packet(pkt.to_bytes())
        assert len(receiver.received) == 2
