"""
Tests for border.storage — chunk encryption + storage proof system
"""
import hashlib
import secrets
import pytest

from border.storage.chunk import Chunk, FileChunker, CHUNK_SIZE
from border.storage.proof import StorageChallenge, StorageProof


class TestChunk:
    def test_chunk_id_is_sha256_of_plaintext(self):
        data = b"hello border storage"
        chunk = Chunk.from_plaintext(file_id="file_001", index=0, data=data)
        assert chunk.chunk_id == hashlib.sha256(data).hexdigest()

    def test_ciphertext_differs_from_plaintext(self):
        data = b"secret data" * 100
        chunk = Chunk.from_plaintext(file_id="file_001", index=0, data=data)
        assert chunk.ciphertext != data

    def test_decrypt_recovers_plaintext(self):
        data = b"round-trip test " * 50
        chunk = Chunk.from_plaintext(file_id="file_001", index=0, data=data)
        # decrypt takes the stored ciphertext as argument
        assert chunk.decrypt(chunk.ciphertext) == data

    def test_tampered_ciphertext_raises(self):
        data = b"tamper test " * 50
        chunk = Chunk.from_plaintext(file_id="file_001", index=0, data=data)
        ct = bytearray(chunk.ciphertext)
        ct[20] ^= 0xFF
        with pytest.raises(Exception):
            chunk.decrypt(bytes(ct))

    def test_same_content_same_id(self):
        data = b"same content"
        c1 = Chunk.from_plaintext("file_a", 0, data)
        c2 = Chunk.from_plaintext("file_b", 0, data)
        assert c1.chunk_id == c2.chunk_id

    def test_size_matches_plaintext(self):
        data = b"x" * 1234
        chunk = Chunk.from_plaintext("f", 0, data)
        assert chunk.size == 1234


class TestFileChunker:
    def test_small_file_single_chunk(self):
        fc = FileChunker()
        chunks = fc.split(b"small" * 100, file_id="f1")
        assert len(chunks) == 1

    def test_large_file_multiple_chunks(self):
        fc = FileChunker()
        data = secrets.token_bytes(CHUNK_SIZE * 3 + 100)
        chunks = fc.split(data, file_id="f2")
        assert len(chunks) == 4

    def test_reassemble_recovers_original(self):
        fc = FileChunker()
        data = secrets.token_bytes(CHUNK_SIZE * 2 + 500)
        chunks = fc.split(data, file_id="f3")
        recovered = fc.reassemble(chunks)
        assert recovered == data

    def test_empty_data_no_chunks(self):
        fc = FileChunker()
        assert fc.split(b"", file_id="f5") == []

    def test_chunk_indices_sequential(self):
        fc = FileChunker()
        data = secrets.token_bytes(CHUNK_SIZE * 2 + 1)
        chunks = fc.split(data, file_id="f6")
        for i, c in enumerate(chunks):
            assert c.index == i

    def test_chunk_file_ids_match(self):
        fc = FileChunker()
        chunks = fc.split(b"abc" * 1000, file_id="myfile")
        assert all(c.file_id == "myfile" for c in chunks)

    def test_reassemble_out_of_order(self):
        fc = FileChunker()
        data = secrets.token_bytes(CHUNK_SIZE * 2 + 1)
        chunks = fc.split(data, file_id="f7")
        assert fc.reassemble(list(reversed(chunks))) == data


class TestStorageChallenge:
    def test_issue_has_nonce(self):
        ch = StorageChallenge.issue(chunk_id="abc123", node_address="BC_node_" + "n" * 32)
        assert len(ch.nonce) > 0

    def test_two_challenges_different_nonce(self):
        ch1 = StorageChallenge.issue(chunk_id="abc", node_address="BC_n" + "a" * 32)
        ch2 = StorageChallenge.issue(chunk_id="abc", node_address="BC_n" + "a" * 32)
        assert ch1.nonce != ch2.nonce

    def test_expected_response_deterministic(self):
        ch = StorageChallenge.issue(chunk_id="xyz", node_address="BC_n" + "b" * 32)
        ct = b"fake ciphertext"
        assert ch.expected_response(ct) == ch.expected_response(ct)

    def test_expected_response_changes_with_ciphertext(self):
        ch = StorageChallenge.issue(chunk_id="xyz", node_address="BC_n" + "b" * 32)
        assert ch.expected_response(b"ct_one") != ch.expected_response(b"ct_two")

    def test_roundtrip(self):
        ch = StorageChallenge.issue(chunk_id="xyz", node_address="BC_n" + "b" * 32)
        ch2 = StorageChallenge.from_dict(ch.to_dict())
        assert ch2.challenge_id == ch.challenge_id
        assert ch2.nonce == ch.nonce


class TestStorageProof:
    def _make_challenge_proof(self):
        data = b"storage proof test data " * 100
        chunk = Chunk.from_plaintext("file_sp", 0, data)
        challenge = StorageChallenge.issue(
            chunk_id=chunk.chunk_id,
            node_address="BC_node_" + "s" * 32,
        )
        response = challenge.expected_response(chunk.ciphertext)
        expected = challenge.expected_response(chunk.ciphertext)
        proof = StorageProof.from_challenge(
            challenge=challenge,
            node_address="BC_node_" + "s" * 32,
            owner_address="BC_owner_" + "o" * 32,
            file_id="file_sp",
            bytes_stored=chunk.size,
            response_hash=response,
            expected_hash=expected,
        )
        return proof, challenge, chunk

    def test_challenge_proof_is_valid(self):
        proof, _, _ = self._make_challenge_proof()
        assert proof.is_valid()

    def test_tampered_response_invalid(self):
        proof, _, _ = self._make_challenge_proof()
        proof.response_hash = "deadbeef" * 8
        assert not proof.is_valid()

    def test_duration_proof_valid(self):
        proof = StorageProof.from_duration(
            node_address="BC_node_" + "d" * 32,
            owner_address="BC_owner_" + "o" * 32,
            chunk_id="chunk_abc",
            file_id="file_dur",
            bytes_stored=4 * 1024 * 1024,
            duration_seconds=86400.0,
        )
        assert proof.is_valid()

    def test_duration_proof_zero_duration_invalid(self):
        proof = StorageProof.from_duration(
            node_address="BC_node_" + "d" * 32,
            owner_address="BC_owner_" + "o" * 32,
            chunk_id="chunk_abc",
            file_id="file_dur",
            bytes_stored=1024,
            duration_seconds=0.0,
        )
        assert not proof.is_valid()

    def test_reward_bc_challenge(self):
        proof, _, _ = self._make_challenge_proof()
        assert proof.reward_bc() > 0

    def test_reward_bc_duration_scales_with_size(self):
        small = StorageProof.from_duration(
            node_address="BC_n" + "d" * 32, owner_address="BC_o" + "o" * 32,
            chunk_id="c1", file_id="f1",
            bytes_stored=1 * 1024 * 1024 * 1024, duration_seconds=86400.0,
        )
        large = StorageProof.from_duration(
            node_address="BC_n" + "d" * 32, owner_address="BC_o" + "o" * 32,
            chunk_id="c2", file_id="f2",
            bytes_stored=10 * 1024 * 1024 * 1024, duration_seconds=86400.0,
        )
        assert large.reward_bc() > small.reward_bc()

    def test_proof_roundtrip(self):
        proof, _, _ = self._make_challenge_proof()
        p2 = StorageProof.from_dict(proof.to_dict())
        assert p2.proof_id == proof.proof_id
        assert p2.response_hash == proof.response_hash
