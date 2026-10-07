# SPDX-FileCopyrightText: 2026 the VSS application contributors
# SPDX-License-Identifier: Apache-2.0
"""Bounded, opt-in RTSP metadata probing without native hardware discovery."""

import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from utils.media_file_info import MediaFileInfo


class RtspProbeTests(unittest.TestCase):
    @patch("utils.media_file_info.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("utils.media_file_info.subprocess.run")
    def test_probe_reads_video_metadata_and_uses_a_bounded_tcp_reader(self, run, _):
        run.return_value = SimpleNamespace(
            stdout=json.dumps(
                {
                    "streams": [
                        {
                            "codec_name": "h264",
                            "width": 960,
                            "height": 540,
                            "r_frame_rate": "25/1",
                            "avg_frame_rate": "0/0",
                        }
                    ]
                }
            )
        )
        info = MediaFileInfo._get_info_rtsp_ffprobe("rtsp://example.test/camera")
        self.assertEqual(info.video_resolution, (960, 540))
        self.assertEqual(info.video_fps, 25)
        self.assertEqual(info.video_codec, "h264")
        command = run.call_args.args[0]
        self.assertIn("tcp", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 12)
        self.assertNotIn("shell", run.call_args.kwargs)

    @patch("utils.media_file_info.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("utils.media_file_info.subprocess.run")
    def test_missing_or_invalid_video_metadata_is_rejected(self, run, _):
        for streams in ([], [{"codec_name": "h264", "width": 0, "height": 540}], [{}]):
            run.return_value = SimpleNamespace(stdout=json.dumps({"streams": streams}))
            with self.subTest(streams=streams), self.assertRaises(ValueError):
                MediaFileInfo._get_info_rtsp_ffprobe("rtsp://example.test/camera")

    @patch("utils.media_file_info.shutil.which", return_value="/usr/bin/ffprobe")
    @patch(
        "utils.media_file_info.subprocess.run",
        side_effect=subprocess.TimeoutExpired("ffprobe", 12),
    )
    def test_timeout_has_a_safe_diagnostic(self, *_):
        with self.assertRaisesRegex(ValueError, "Could not probe") as caught:
            MediaFileInfo._get_info_rtsp_ffprobe(
                "rtsp://example.test/camera", "user", "password"
            )
        self.assertNotIn("password", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    @patch.dict("os.environ", {}, clear=True)
    @patch.object(MediaFileInfo, "_get_info_gst", return_value="legacy")
    def test_default_backend_is_preserved(self, probe):
        self.assertEqual(MediaFileInfo.get_info("rtsp://example.test/camera"), "legacy")
        probe.assert_called_once()

    @patch.dict("os.environ", {"RTVI_RTSP_PROBE_BACKEND": "ffprobe"})
    @patch.object(MediaFileInfo, "_get_info_rtsp_ffprobe", return_value="bounded")
    def test_ffprobe_backend_is_explicitly_selected(self, probe):
        self.assertEqual(
            MediaFileInfo.get_info("rtsp://example.test/camera"), "bounded"
        )
        probe.assert_called_once()


if __name__ == "__main__":
    unittest.main()
