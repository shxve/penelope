"""
WebSocketConn framer unit tests.

Pure stdlib. Exercises WebSocketConn.frame() and WebSocketConn.recv() in
isolation using a fake socket. Integration tests against a real client
(with the `websockets` library) live in a follow-up commit.

Run: python3 -m unittest test.test_ws_framer
Or:  python3 test/test_ws_framer.py
"""

import os
import struct
import sys
import unittest

# Import penelope from the repo root without installing it
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
import penelope  # noqa: E402
from penelope import (  # noqa: E402
	WebSocketConn,
	WS_OP_BINARY, WS_OP_TEXT, WS_OP_CONT,
	WS_OP_PING, WS_OP_PONG, WS_OP_CLOSE,
	WS_MAX_FRAME_PAYLOAD,
)

# WebSocketConn reads options.network_buffer_size for its per-call TCP recv
# size; ensure the module-level singleton exists (main() normally sets it).
if not hasattr(penelope, "options") or penelope.options is None:
	penelope.options = penelope.Options()


def client_frame(op, payload, mask_key=b"\xaa\xbb\xcc\xdd", fin=True):
	"""Build a masked client->server frame the way a real WS peer would."""
	b1 = (0x80 if fin else 0x00) | (op & 0x0F)
	n = len(payload)
	if n < 126:
		hdr = struct.pack("!BB", b1, 0x80 | n)
	elif n < (1 << 16):
		hdr = struct.pack("!BBH", b1, 0x80 | 126, n)
	else:
		hdr = struct.pack("!BBQ", b1, 0x80 | 127, n)
	masked = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
	return hdr + mask_key + masked


class FakeSocket:
	"""Minimal recv/send/setsockopt/setblocking/fileno stand-in for tests."""
	def __init__(self):
		self.inbox = bytearray()   # bytes to feed to recv()
		self.outbox = bytearray()  # bytes captured from send()
		self.closed = False
	def feed(self, data):
		self.inbox.extend(data)
	def recv(self, n):
		if not self.inbox:
			raise BlockingIOError()
		chunk = bytes(self.inbox[:n])
		del self.inbox[:n]
		return chunk
	def send(self, data):
		self.outbox.extend(data)
		return len(data)
	def setsockopt(self, *a, **kw): pass
	def setblocking(self, f): pass
	def fileno(self): return -1
	def getpeername(self): return ("127.0.0.1", 0)
	def getsockname(self): return ("127.0.0.1", 0)
	def close(self): self.closed = True


class TestFrameBuilder(unittest.TestCase):

	def test_small_payload_uses_7bit_length(self):
		out = WebSocketConn.frame(b"hi", WS_OP_BINARY)
		# 0x82 = FIN=1 opcode=BINARY, 0x02 = len 2, then payload; no mask
		self.assertEqual(out, b"\x82\x02hi")

	def test_medium_payload_uses_16bit_length(self):
		payload = b"a" * 200
		out = WebSocketConn.frame(payload, WS_OP_BINARY)
		self.assertEqual(out[0], 0x82)
		self.assertEqual(out[1], 126)
		length, = struct.unpack("!H", out[2:4])
		self.assertEqual(length, 200)
		self.assertEqual(out[4:], payload)

	def test_large_payload_uses_64bit_length(self):
		payload = b"x" * 70_000
		out = WebSocketConn.frame(payload, WS_OP_BINARY)
		self.assertEqual(out[0], 0x82)
		self.assertEqual(out[1], 127)
		length, = struct.unpack("!Q", out[2:10])
		self.assertEqual(length, 70_000)
		self.assertEqual(out[10:], payload)

	def test_server_frames_are_unmasked(self):
		# byte 2 top bit MUST be 0 (server MUST NOT mask; RFC 6455 5.1)
		out = WebSocketConn.frame(b"x", WS_OP_BINARY)
		self.assertEqual(out[1] & 0x80, 0)


class TestFramerRecv(unittest.TestCase):

	def _new(self):
		sock = FakeSocket()
		return sock, WebSocketConn(sock)

	def test_small_binary_frame_decoded(self):
		sock, conn = self._new()
		sock.feed(client_frame(WS_OP_BINARY, b"hello"))
		self.assertEqual(conn.recv(1024), b"hello")

	def test_text_opcode_returned_as_bytes(self):
		sock, conn = self._new()
		sock.feed(client_frame(WS_OP_TEXT, "héllo".encode("utf-8")))
		self.assertEqual(conn.recv(1024), "héllo".encode("utf-8"))

	def test_unmasked_client_frame_raises(self):
		sock, conn = self._new()
		# hand-build an UNMASKED frame (mask bit off in byte 2)
		sock.feed(b"\x82\x02hi")
		with self.assertRaises(OSError):
			conn.recv(1024)

	def test_reserved_bits_rejected(self):
		sock, conn = self._new()
		# RSV1 set (0x40): would signal permessage-deflate we didn't negotiate
		sock.feed(b"\xc2\x82\xaa\xbb\xcc\xdd" + bytes(2))
		with self.assertRaises(OSError):
			conn.recv(1024)

	def test_oversized_frame_rejected(self):
		sock, conn = self._new()
		# advertise a length above the cap without providing the bytes
		hdr = struct.pack("!BBQ", 0x82, 0x80 | 127, WS_MAX_FRAME_PAYLOAD + 1)
		sock.feed(hdr + b"\x00" * 4)  # mask key; no payload
		with self.assertRaises(OSError):
			conn.recv(1024)

	def test_control_frame_fragmented_rejected(self):
		sock, conn = self._new()
		sock.feed(client_frame(WS_OP_PING, b"", fin=False))
		with self.assertRaises(OSError):
			conn.recv(1024)

	def test_control_frame_oversized_rejected(self):
		sock, conn = self._new()
		# ping with a 126-byte payload — over the 125-byte control limit
		sock.feed(client_frame(WS_OP_PING, b"x" * 126))
		with self.assertRaises(OSError):
			conn.recv(1024)

	def test_partial_frame_raises_blocking(self):
		sock, conn = self._new()
		full = client_frame(WS_OP_BINARY, b"hello, penelope")
		# feed only the first 6 bytes — header + partial payload
		sock.feed(full[:6])
		with self.assertRaises(BlockingIOError):
			conn.recv(1024)
		# now the rest arrives on the next TCP event
		sock.feed(full[6:])
		self.assertEqual(conn.recv(1024), b"hello, penelope")

	def test_client_fragmentation_reassembled(self):
		sock, conn = self._new()
		sock.feed(client_frame(WS_OP_BINARY, b"first-half-", fin=False))
		sock.feed(client_frame(WS_OP_CONT,   b"second-half", fin=True))
		# stream semantics: single recv() drains the whole reassembled payload
		self.assertEqual(conn.recv(1024), b"first-half-second-half")

	def test_continuation_without_start_rejected(self):
		sock, conn = self._new()
		sock.feed(client_frame(WS_OP_CONT, b"orphan", fin=True))
		with self.assertRaises(OSError):
			conn.recv(1024)

	def test_new_message_during_continuation_rejected(self):
		sock, conn = self._new()
		sock.feed(client_frame(WS_OP_BINARY, b"pending", fin=False))
		sock.feed(client_frame(WS_OP_BINARY, b"other",   fin=True))
		with self.assertRaises(OSError):
			conn.recv(1024)

	def test_ping_queues_pong(self):
		sock, conn = self._new()
		sock.feed(client_frame(WS_OP_PING, b"peekaboo"))
		with self.assertRaises(BlockingIOError):
			conn.recv(1024)
		self.assertTrue(conn.has_pending_ctrl())
		# next send drains the queued PONG; caller data appended afterward
		self.assertEqual(conn.send(b"shell-output"), len(b"shell-output"))
		self.assertIn(b"peekaboo", bytes(sock.outbox))
		self.assertTrue(sock.outbox.endswith(b"shell-output"))
		self.assertFalse(conn.has_pending_ctrl())

	def test_close_frame_marks_peer_closed(self):
		sock, conn = self._new()
		sock.feed(client_frame(WS_OP_CLOSE, b"\x03\xe8"))  # code 1000
		self.assertEqual(conn.recv(1024), b"")
		self.assertTrue(conn._peer_closed)
		self.assertTrue(conn.has_pending_ctrl())

	def test_recv_drains_full_payload_ignoring_n(self):
		# The load-bearing contract: even if a caller asks for a tiny n,
		# recv() returns the whole decoded payload in one shot, so leftover
		# bytes never sit marooned behind a fileno() that only reflects TCP.
		sock, conn = self._new()
		sock.feed(client_frame(WS_OP_BINARY, b"x" * 5000))
		got = conn.recv(1)
		self.assertEqual(len(got), 5000)

	def test_tcp_closed_returns_empty(self):
		sock, conn = self._new()
		# FakeSocket with empty inbox raises BlockingIOError; simulate TCP
		# close by overriding recv to return b"" instead
		def closed_recv(n): return b""
		sock.recv = closed_recv
		self.assertEqual(conn.recv(1024), b"")


class TestSendPartialCtrl(unittest.TestCase):
	"""send() semantics when TCP would block partway through the queued
	control-frame bytes: caller sees BlockingIOError; no caller bytes lost."""

	def test_partial_ctrl_send_raises_and_preserves_data(self):
		sock, conn = self._new_with_partial_send()
		# force a pending PONG
		sock.feed(client_frame(WS_OP_PING, b"p"))
		with self.assertRaises(BlockingIOError):
			conn.recv(1024)
		self.assertTrue(conn.has_pending_ctrl())
		# TCP accepts only 2 bytes at a time; the PONG frame is >2 bytes so
		# send() should raise BlockingIOError without accepting caller data
		with self.assertRaises(BlockingIOError):
			conn.send(b"caller-data")
		# ctrl still has some bytes, caller data still owed by ITS caller
		self.assertTrue(conn.has_pending_ctrl())
		self.assertNotIn(b"caller-data", bytes(sock.outbox))

	def _new_with_partial_send(self):
		sock = FakeSocket()
		def partial(data):
			take = min(2, len(data))
			sock.outbox.extend(data[:take])
			return take
		sock.send = partial
		return sock, WebSocketConn(sock)


if __name__ == "__main__":
	unittest.main(verbosity=2)
