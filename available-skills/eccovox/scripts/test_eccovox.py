import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import eccovox


class EccoVoxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.read_root = self.root / "read"
        self.write_root = self.root / "write"
        self.read_root.mkdir()
        self.write_root.mkdir()
        self.profile = self.root / "eccovox.toml"
        self.profile.write_text(
            "[defaults]\nrequest_timeout_seconds = 120\nmax_audio_bytes = 10485760\nmax_text_characters = 4000\n\n"
            "[profiles.example]\nbase_url = \"http://127.0.0.1:8870\"\n"
            f"readable_roots = [\"{self.read_root.as_posix()}\"]\nwritable_roots = [\"{self.write_root.as_posix()}\"]\n",
            encoding="utf-8",
        )
        self.config = eccovox.load_config(str(self.profile), "example")

    def tearDown(self):
        self.temp.cleanup()

    def test_profile_rejects_remote_endpoint(self):
        content = self.profile.read_text(encoding="utf-8").replace("http://127.0.0.1:8870", "https://speech.example.test")
        self.profile.write_text(content, encoding="utf-8")
        with self.assertRaisesRegex(eccovox.SafeError, "loopback"):
            eccovox.load_config(str(self.profile), "example")

    def test_request_version_and_unknown_operation_are_rejected(self):
        with self.assertRaisesRegex(eccovox.SafeError, "version"):
            eccovox.execute(self.config, {"version": 2, "operation": "health.get"})
        with self.assertRaisesRegex(eccovox.SafeError, "Unsupported"):
            eccovox.execute(self.config, {"version": 1, "operation": "delete.everything"})

    @patch("eccovox._json_response")
    def test_transcription_uses_multipart_and_returns_safe_fields(self, request):
        audio = self.read_root / "sample.ogg"
        audio.write_bytes(b"sound")
        request.return_value = {"text": "Olá", "language": "pt-BR", "durationMillis": 8}
        result = eccovox.transcribe(self.config, {"audio_path": str(audio), "language": "pt-BR"})
        self.assertEqual(result, {"text": "Olá", "language": "pt-BR", "duration_millis": 8})
        endpoint, body, content_type = request.call_args.args[1:]
        self.assertEqual(endpoint, "/v1/audio/transcriptions")
        self.assertIn(b"sample.ogg", body)
        self.assertTrue(content_type.startswith("multipart/form-data"))

    def test_transcription_rejects_path_outside_profile(self):
        denied = self.root / "outside.ogg"
        denied.write_bytes(b"sound")
        with self.assertRaisesRegex(eccovox.SafeError, "allowed roots"):
            eccovox.transcribe(self.config, {"audio_path": str(denied)})

    @patch("eccovox._request")
    def test_synthesis_requires_confirmation_and_writes_allowed_output(self, request):
        output = self.write_root / "voice.mp3"
        with self.assertRaisesRegex(eccovox.SafeError, "confirm"):
            eccovox.synthesize(self.config, {"text": "Olá", "output_path": str(output)})
        request.return_value = (b"audio", "audio/mpeg")
        result = eccovox.synthesize(self.config, {"text": "Olá", "output_path": str(output), "confirm": True})
        self.assertEqual(output.read_bytes(), b"audio")
        self.assertEqual(result["content_type"], "audio/mpeg")
        self.assertEqual(request.call_args.args[1], "/v1/audio/speech")

    @patch("eccovox._json_response")
    def test_health_routes_to_runtime(self, request):
        request.return_value = {"status": "ready", "version": "1.0", "capabilities": {"stt": {"status": "ready"}}}
        result = eccovox.execute(self.config, {"version": 1, "operation": "health.get"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["status"], "ready")

    @patch("eccovox.execute")
    def test_main_accepts_powershell_utf8_bom(self, execute):
        execute.return_value = {"version": 1, "ok": True, "operation": "health.get", "data": {}}
        with patch("sys.stdin", StringIO("\ufeff{\"version\":1,\"operation\":\"health.get\"}")), patch("sys.argv", ["eccovox.py", "--config", str(self.profile), "--profile", "example"]):
            self.assertEqual(eccovox.main(), 0)
        execute.assert_called_once()

    @patch("eccovox.execute")
    def test_main_decodes_utf8_bom_from_binary_stdin(self, execute):
        class BinaryInput:
            class Buffer:
                @staticmethod
                def read():
                    return b"\xef\xbb\xbf{\"version\":1,\"operation\":\"health.get\"}"

            buffer = Buffer()

        execute.return_value = {"version": 1, "ok": True, "operation": "health.get", "data": {}}
        with patch("sys.stdin", BinaryInput()), patch("sys.argv", ["eccovox.py", "--config", str(self.profile), "--profile", "example"]):
            self.assertEqual(eccovox.main(), 0)
        execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
