"""
WSListener integration tests.

Uses the `websockets` library at test time only (Penelope stays stdlib-only
at runtime). Skipped if websockets is not installed.

Run: python3 -m unittest test.test_ws_listener
"""

import asyncio
import os
import re
import select as _sel
import socket
import sys
import threading
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
import penelope  # noqa: E402
from penelope import WSListener, WebSocketConn  # noqa: E402

try:
	import websockets
except ImportError:
	websockets = None

if not hasattr(penelope, "options") or penelope.options is None:
	penelope.options = penelope.Options()


def _echo_once(listener, results, timeout=5.0):
	"""Accept ONE connection, handshake, echo binary frames until peer closes.

	Mirrors Session's outbuf model: loop on partial TCP send. A naive
	fire-and-forget send() would drop data past the kernel send-buffer
	limit whenever the echoed frame exceeded ~64 KiB.
	"""
	try:
		listener.socket.settimeout(timeout)
		raw, peer = listener.socket.accept()
		conn = listener.accept_ws(raw, peer)
		results["conn"] = conn
		outbuf = bytearray()
		while True:
			rlist = [conn]
			wlist = [conn] if outbuf else []
			r, w, _ = _sel.select(rlist, wlist, [], timeout)
			if not r and not w:
				break
			if r:
				try:
					data = conn.recv(4096)
				except BlockingIOError:
					data = None
				else:
					if not data:
						break
					outbuf.extend(WebSocketConn.frame(data))
			if outbuf:
				try:
					sent = conn.send(bytes(outbuf))
					del outbuf[:sent]
				except BlockingIOError:
					pass  # try again on next writable event
		conn.close()
	except Exception as e:
		results["err"] = e


@unittest.skipIf(websockets is None, "websockets library not installed")
class TestWSListenerHappyPath(unittest.TestCase):

	def setUp(self):
		self.listener = WSListener(host="127.0.0.1", port=0)
		self.results = {}
		self.t = threading.Thread(target=_echo_once, args=(self.listener, self.results))
		self.t.start()

	def tearDown(self):
		self.t.join(timeout=6.0)
		self.listener.close()

	def test_handshake_and_binary_echo(self):
		port = self.listener.port

		async def client():
			async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
				await ws.send(b"hello, ws-listener")
				echo = await ws.recv()
				self.assertEqual(echo, b"hello, ws-listener")

		asyncio.run(asyncio.wait_for(client(), timeout=5.0))
		self.assertNotIn("err", self.results, "server: %r" % self.results.get("err"))

	def test_large_payload_roundtrip(self):
		# 40 KiB — well under WS_MAX_FRAME_PAYLOAD (64 KiB) but above the
		# 126-byte 7-bit-length threshold, exercising the 16-bit length path.
		# The full oversized-frame rejection is asserted by the framer suite.
		port = self.listener.port
		payload = os.urandom(40_000)

		async def client():
			async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
				await ws.send(payload)
				buf = b""
				while len(buf) < len(payload):
					buf += await ws.recv()
				self.assertEqual(buf, payload)

		asyncio.run(asyncio.wait_for(client(), timeout=5.0))
		self.assertNotIn("err", self.results, "server: %r" % self.results.get("err"))


class TestWSListenerRejects(unittest.TestCase):
	"""Rejection paths use a bare TCP client so we can inspect the HTTP status
	the listener replies with. No websockets library needed."""

	def _serve_one_and_expect_error(self, listener, expected_substr):
		results = {}
		def worker():
			try:
				listener.socket.settimeout(3.0)
				raw, peer = listener.socket.accept()
				listener.accept_ws(raw, peer)
				results["conn_ok"] = True  # shouldn't reach here
			except OSError as e:
				results["err"] = str(e)
		t = threading.Thread(target=worker)
		t.start()
		return t, results

	def _http_read_status(self, host, port, request_bytes):
		sock = socket.create_connection((host, port), timeout=3.0)
		sock.sendall(request_bytes)
		# grab enough to see the status line
		buf = b""
		while b"\r\n" not in buf and len(buf) < 4096:
			chunk = sock.recv(1024)
			if not chunk:
				break
			buf += chunk
		sock.close()
		status_line = buf.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
		return status_line, buf

	def test_wrong_path_rejected_404(self):
		lst = WSListener(host="127.0.0.1", port=0, path="/expected")
		try:
			t, results = self._serve_one_and_expect_error(lst, "path mismatch")
			req = (b"GET /wrong HTTP/1.1\r\n"
			       b"Host: h\r\n"
			       b"Upgrade: websocket\r\n"
			       b"Connection: Upgrade\r\n"
			       b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
			       b"Sec-WebSocket-Version: 13\r\n\r\n")
			status, _ = self._http_read_status("127.0.0.1", lst.port, req)
			self.assertIn("404", status)
			t.join(timeout=3.0)
			self.assertIn("path mismatch", results.get("err", ""))
		finally:
			lst.close()

	def test_host_pattern_rejected_404(self):
		pat = re.compile(r"^good\.example\.com$")
		lst = WSListener(host="127.0.0.1", port=0, host_pattern=pat)
		try:
			t, results = self._serve_one_and_expect_error(lst, "host header rejected")
			req = (b"GET / HTTP/1.1\r\n"
			       b"Host: evil.example.com\r\n"
			       b"Upgrade: websocket\r\n"
			       b"Connection: Upgrade\r\n"
			       b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
			       b"Sec-WebSocket-Version: 13\r\n\r\n")
			status, _ = self._http_read_status("127.0.0.1", lst.port, req)
			self.assertIn("404", status)
			t.join(timeout=3.0)
			self.assertIn("host header rejected", results.get("err", ""))
		finally:
			lst.close()

	def test_missing_upgrade_rejected_400(self):
		lst = WSListener(host="127.0.0.1", port=0)
		try:
			t, results = self._serve_one_and_expect_error(lst, "not a ws upgrade")
			req = (b"GET / HTTP/1.1\r\n"
			       b"Host: h\r\n\r\n")
			status, _ = self._http_read_status("127.0.0.1", lst.port, req)
			self.assertIn("400", status)
			t.join(timeout=3.0)
			self.assertIn("not a ws upgrade", results.get("err", ""))
		finally:
			lst.close()

	def test_bad_method_rejected_405(self):
		lst = WSListener(host="127.0.0.1", port=0)
		try:
			t, results = self._serve_one_and_expect_error(lst, "bad method")
			req = (b"POST / HTTP/1.1\r\n"
			       b"Host: h\r\n"
			       b"Upgrade: websocket\r\n"
			       b"Connection: Upgrade\r\n"
			       b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
			       b"Sec-WebSocket-Version: 13\r\n\r\n")
			status, _ = self._http_read_status("127.0.0.1", lst.port, req)
			self.assertIn("405", status)
			t.join(timeout=3.0)
			self.assertIn("bad method", results.get("err", ""))
		finally:
			lst.close()

	def test_wrong_ws_version_rejected_400(self):
		lst = WSListener(host="127.0.0.1", port=0)
		try:
			t, results = self._serve_one_and_expect_error(lst, "unsupported ws version")
			req = (b"GET / HTTP/1.1\r\n"
			       b"Host: h\r\n"
			       b"Upgrade: websocket\r\n"
			       b"Connection: Upgrade\r\n"
			       b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
			       b"Sec-WebSocket-Version: 8\r\n\r\n")
			status, _ = self._http_read_status("127.0.0.1", lst.port, req)
			self.assertIn("400", status)
			t.join(timeout=3.0)
			self.assertIn("unsupported ws version", results.get("err", ""))
		finally:
			lst.close()


# ---------------------------------------------------------------------------
# --ws-backend decoy reverse-proxy
# ---------------------------------------------------------------------------

from http.server import BaseHTTPRequestHandler, HTTPServer
import threading as _thr


class _DecoyHandler(BaseHTTPRequestHandler):
	CANNED = b"<html><body>welcome to the decoy</body></html>"

	def log_message(self, *a, **kw): pass  # silence

	def do_GET(self):
		self.send_response(200)
		self.send_header("Content-Type", "text/html")
		self.send_header("Content-Length", str(len(self.CANNED)))
		# echo path back in a header so tests can assert it was preserved
		self.send_header("X-Decoy-Path", self.path)
		self.end_headers()
		self.wfile.write(self.CANNED)


class _DecoyServer:
	def __init__(self):
		self.srv = HTTPServer(("127.0.0.1", 0), _DecoyHandler)
		self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
		self.t = _thr.Thread(target=self.srv.serve_forever, daemon=True)
		self.t.start()

	def stop(self):
		self.srv.shutdown()
		self.srv.server_close()


class TestWSListenerBackend(unittest.TestCase):

	def setUp(self):
		self.decoy = _DecoyServer()

	def tearDown(self):
		self.decoy.stop()

	def _serve_one_expect_error(self, listener):
		results = {}
		def worker():
			try:
				listener.socket.settimeout(3.0)
				raw, peer = listener.socket.accept()
				listener.accept_ws(raw, peer)
			except OSError as e:
				results["err"] = str(e)
		t = _thr.Thread(target=worker); t.start()
		return t, results

	def _http_get_full(self, host, port, req_bytes):
		"""Return (status_line, headers_dict, body)."""
		sock = socket.create_connection((host, port), timeout=3.0)
		sock.sendall(req_bytes)
		chunks = []
		while True:
			try:
				chunk = sock.recv(4096)
			except socket.timeout:
				break
			if not chunk:
				break
			chunks.append(chunk)
			# stop as soon as we've read the whole response — the server
			# sends Connection: close, but stop earlier if headers+body seen
			if len(b"".join(chunks)) > 65536:
				break
		sock.close()
		raw = b"".join(chunks)
		head, _, body = raw.partition(b"\r\n\r\n")
		status = head.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
		hdrs = {}
		for line in head.split(b"\r\n")[1:]:
			if b":" in line:
				k, v = line.split(b":", 1)
				hdrs[k.strip().decode("ascii").lower()] = v.strip().decode("iso-8859-1")
		return status, hdrs, body

	def test_non_ws_get_proxies_to_backend(self):
		lst = WSListener(host="127.0.0.1", port=0, backend_url=self.decoy.url)
		try:
			t, _ = self._serve_one_expect_error(lst)
			req = b"GET /some/api HTTP/1.1\r\nHost: cover.example.com\r\nUser-Agent: probe\r\n\r\n"
			status, hdrs, body = self._http_get_full("127.0.0.1", lst.port, req)
			self.assertIn("200", status)
			self.assertIn(b"welcome to the decoy", body)
			self.assertEqual(hdrs.get("x-decoy-path"), "/some/api")
			t.join(timeout=3.0)
		finally:
			lst.close()

	def test_wrong_path_falls_through_to_backend(self):
		lst = WSListener(host="127.0.0.1", port=0, path="/expected",
		                 backend_url=self.decoy.url)
		try:
			t, _ = self._serve_one_expect_error(lst)
			# WS-shaped request but at the WRONG path → should be decoyed
			req = (b"GET /wrong HTTP/1.1\r\n"
			       b"Host: h\r\n"
			       b"Upgrade: websocket\r\n"
			       b"Connection: Upgrade\r\n"
			       b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
			       b"Sec-WebSocket-Version: 13\r\n\r\n")
			status, hdrs, body = self._http_get_full("127.0.0.1", lst.port, req)
			self.assertIn("200", status)
			self.assertIn(b"welcome", body)
			self.assertEqual(hdrs.get("x-decoy-path"), "/wrong")
			t.join(timeout=3.0)
		finally:
			lst.close()

	def test_ws_upgrade_still_wins_when_backend_configured(self):
		if websockets is None:
			self.skipTest("websockets library not installed")
		lst = WSListener(host="127.0.0.1", port=0, backend_url=self.decoy.url)
		results = {}

		def worker():
			try:
				lst.socket.settimeout(3.0)
				raw, peer = lst.socket.accept()
				conn = lst.accept_ws(raw, peer)
				results["ok"] = True
				# just close; client only cares that handshake completed
				conn.close()
			except Exception as e:
				results["err"] = str(e)

		try:
			t = _thr.Thread(target=worker); t.start()

			async def probe():
				async with websockets.connect("ws://127.0.0.1:%d/" % lst.port,
				                              open_timeout=3.0) as ws:
					pass

			asyncio.run(asyncio.wait_for(probe(), timeout=5.0))
			t.join(timeout=3.0)
			self.assertTrue(results.get("ok"), "server: %r" % results.get("err"))
		finally:
			lst.close()


if __name__ == "__main__":
	unittest.main(verbosity=2)
