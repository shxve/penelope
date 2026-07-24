"""
CLI wiring tests: `--ws` and friends must land as the right fields on
penelope.Options, and Options must default to raw-TCP when --ws is off.

The end-to-end run (subprocess spawning penelope.py --ws, real WS client
connecting, seeing the log line) needs a controlling tty because
listener_menu() calls tty.setraw(sys.stdin); we've verified that path
manually. This file only asserts the argparse -> Options plumbing.

Run: python3 -m unittest test.test_ws_cli
"""

import os
import sys
import unittest
from argparse import ArgumentParser

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
import penelope  # noqa: E402


def _reparse(args):
	"""Rebuild just enough of main()'s parser to exercise the WS group."""
	options = penelope.Options()
	parser = ArgumentParser(add_help=False)
	parser.add_argument("-p", "--ports")
	parser.add_argument("args", nargs="*")
	parser.add_argument("-i", "--interface")
	parser.add_argument("-c", "--connect")
	parser.add_argument("-j", "--jump", action="append")
	parser.add_argument("-a", "--payloads", action="store_true")
	parser.add_argument("-l", "--interfaces", action="store_true")
	parser.add_argument("-L", "--no-log", action="store_true")
	parser.add_argument("-T", "--no-timestamps", action="store_true")
	parser.add_argument("-CT", "--no-colored-timestamps", action="store_true")
	parser.add_argument("-M", "--menu", action="store_true")
	parser.add_argument("-m", "--maintain", type=int)
	parser.add_argument("-S", "--single-session", action="store_true")
	parser.add_argument("-ms", "--max-sessions", type=int)
	parser.add_argument("-C", "--no-attach", action="store_true")
	parser.add_argument("-U", "--no-upgrade", action="store_true")
	parser.add_argument("-H", "--keep-history", action="store_true")
	parser.add_argument("-O", "--oscp-safe", action="store_true")
	parser.add_argument("--no-disk", action="store_true")
	parser.add_argument("--mcp", action="store_true")
	parser.add_argument("--mcp-host", type=str)
	parser.add_argument("--mcp-port", type=int)
	parser.add_argument("--mcp-token", type=str)
	# --- WS group under test ---
	parser.add_argument("--ws", action="store_true")
	parser.add_argument("--ws-path", type=str)
	parser.add_argument("--ws-host", type=str)
	parser.add_argument("--ws-backend", type=str)
	parser.add_argument("--tls-cert", type=str)
	parser.add_argument("--tls-key", type=str)
	# ---
	parser.add_argument("-s", "--serve", action="store_true")
	parser.add_argument("-prefix", "--url-prefix", type=str)
	parser.add_argument("-u", "--upload", action="store_true")
	parser.add_argument("-ud", "--upload-dir", type=str)
	parser.add_argument("-N", "--no-bins")
	parser.add_argument("-v", "--version", action="store_true")
	parser.add_argument("-d", "--debug", action="store_true")
	parser.add_argument("-dd", "--dev-mode", action="store_true")
	parser.add_argument("-cu", "--check-urls", action="store_true")
	parser.parse_args(args, options)
	return options


class TestWSOptionsPlumbing(unittest.TestCase):

	def test_defaults_leave_ws_off(self):
		opts = _reparse([])
		self.assertFalse(opts.ws)
		self.assertEqual(opts.ws_path, "/")
		self.assertEqual(opts.ws_host, "")
		self.assertEqual(opts.ws_backend, "")
		self.assertEqual(opts.tls_cert, "")
		self.assertEqual(opts.tls_key, "")

	def test_ws_backend_lands_on_options(self):
		opts = _reparse(["--ws", "--ws-backend", "https://decoy.example.com"])
		self.assertEqual(opts.ws_backend, "https://decoy.example.com")

	def test_ws_flag_flips_field(self):
		opts = _reparse(["--ws"])
		self.assertTrue(opts.ws)

	def test_ws_path_lands_on_options(self):
		opts = _reparse(["--ws", "--ws-path", "/xk9"])
		self.assertTrue(opts.ws)
		self.assertEqual(opts.ws_path, "/xk9")

	def test_ws_host_lands_on_options(self):
		opts = _reparse(["--ws", "--ws-host", "^good\\..*$"])
		self.assertEqual(opts.ws_host, "^good\\..*$")

	def test_tls_pair_lands_on_options(self):
		opts = _reparse(["--ws", "--tls-cert", "/x/c.pem", "--tls-key", "/x/k.pem"])
		self.assertEqual(opts.tls_cert, "/x/c.pem")
		self.assertEqual(opts.tls_key, "/x/k.pem")


if __name__ == "__main__":
	unittest.main(verbosity=2)
