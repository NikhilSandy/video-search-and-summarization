# SPDX-FileCopyrightText: Copyright (c) 2023-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Media File Info."""

import asyncio
import concurrent.futures
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from urllib.parse import quote, urlsplit, urlunsplit

import gi
from pymediainfo import MediaInfo

gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")

from gi.repository import Gst  # noqa: E402
from gi.repository import GstPbutils  # noqa: E402

Gst.init(None)

# Dedicated thread pool for media info extraction
# Allows concurrent ffprobe operations without blocking the default thread pool
_media_info_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=10, thread_name_prefix="mediainfo_"
)


def _to_float(value, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(str(value).split()[0])
        except (IndexError, ValueError):
            return default


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(_to_float(value, float(default)))


@dataclass
class MediaFileInfo:
    is_image: bool = False
    video_codec: str = ""
    video_duration_nsec: int = 0
    video_fps: float = 0.0
    video_frame_count: int = 0
    video_resolution: tuple[int, int] = (0, 0)

    @staticmethod
    def _get_info_gst(uri_or_file: str, username="", password=""):
        uri_or_file = str(uri_or_file)
        media_file_info = MediaFileInfo()

        if uri_or_file.startswith("rtsp://") or uri_or_file.startswith("file://"):
            uri = uri_or_file
        else:
            uri = "file://" + os.path.abspath(str(uri_or_file))

        def select_stream(source, idx, caps):
            if "audio" in caps.to_string():
                return False
            return True

        def source_setup(discoverer, source):
            if uri.startswith("rtsp://"):
                source.connect("select-stream", select_stream)
                source.set_property("timeout", 1000000)
                if username and password:
                    source.set_property("user-id", username)
                    source.set_property("user-pw", password)

        discoverer = GstPbutils.Discoverer()
        discoverer.connect("source-setup", source_setup)

        try:
            file_info = discoverer.discover_uri(uri)
        except gi.repository.GLib.GError as e:
            raise Exception("Unsupported file type - " + uri + " Error:" + str(e))
        for stream_info in file_info.get_stream_list():
            if isinstance(stream_info, GstPbutils.DiscovererVideoInfo):
                media_file_info.video_duration_nsec = int(file_info.get_duration())
                media_file_info.video_codec = str(
                    GstPbutils.pb_utils_get_codec_description(stream_info.get_caps())
                )
                media_file_info.video_resolution = (
                    _to_int(stream_info.get_width()),
                    _to_int(stream_info.get_height()),
                )
                framerate_denom = stream_info.get_framerate_denom()
                if framerate_denom:
                    media_file_info.video_fps = float(
                        stream_info.get_framerate_num() / framerate_denom
                    )
                else:
                    media_file_info.video_fps = 0.0
                media_file_info.is_image = bool(stream_info.is_image())
                break
        return media_file_info

    @staticmethod
    def _get_info_mediainfo(uri_or_file: str):
        if uri_or_file.startswith("file://"):
            file = uri_or_file[7:]
        else:
            file = uri_or_file

        media_file_info = MediaFileInfo()
        media_info = MediaInfo.parse(file)
        general_duration = 0.0
        for track in media_info.tracks:
            if track.track_type == "General":
                general_duration = _to_float(getattr(track, "duration", None))
                break

        have_image_or_video = False
        for track in media_info.tracks:
            if track.track_type == "Video":
                media_file_info.is_image = False
                media_file_info.video_codec = getattr(track, "format", "") or ""
                duration = _to_float(getattr(track, "duration", None), general_duration)
                media_file_info.video_duration_nsec = int(duration * 1000000)
                media_file_info.video_fps = _to_float(
                    getattr(track, "frame_rate", None)
                    or getattr(track, "original_frame_rate", None)
                )
                media_file_info.video_frame_count = _to_int(
                    getattr(track, "frame_count", None)
                )
                media_file_info.video_resolution = (
                    _to_int(getattr(track, "width", 0)),
                    _to_int(getattr(track, "height", 0)),
                )
                have_image_or_video = True
                return media_file_info
            if track.track_type == "Image":
                media_file_info.is_image = True
                media_file_info.video_codec = getattr(track, "format", "") or ""
                media_file_info.video_duration_nsec = 0
                media_file_info.video_fps = 0.0
                media_file_info.video_resolution = (
                    _to_int(getattr(track, "width", 0)),
                    _to_int(getattr(track, "height", 0)),
                )
                have_image_or_video = True

        if not have_image_or_video:
            raise Exception("Unsupported file type - " + file)
        return media_file_info

    @staticmethod
    def get_info(uri_or_file: str, username="", password=""):
        if str(uri_or_file).startswith("rtsp://"):
            if os.environ.get("RTVI_RTSP_PROBE_BACKEND") == "ffprobe":
                return MediaFileInfo._get_info_rtsp_ffprobe(
                    str(uri_or_file), username, password
                )
            return MediaFileInfo._get_info_gst(uri_or_file, username, password)
        else:
            return MediaFileInfo._get_info_mediainfo(str(uri_or_file))

    @staticmethod
    def _get_info_rtsp_ffprobe(uri: str, username="", password=""):
        """Probe RTSP metadata with a bounded TCP reader, without GPU decoding.

        Opt-in alternative for hardware-backed Gst discovery that can block
        before a live stream is admitted. Preserve credentials without logging
        subprocess stderr, which can echo authenticated source URLs.
        """
        binary = shutil.which("ffprobe")
        codec_binary = "/opt/nvidia/rtvi/codecs/usr/bin/ffprobe"
        if not binary and os.access(codec_binary, os.X_OK):
            binary = codec_binary
        if not binary:
            raise ValueError("FFprobe is required for RTVI_RTSP_PROBE_BACKEND=ffprobe")
        if username and password:
            parts = urlsplit(uri)
            if parts.username is not None:
                raise ValueError("RTSP credentials must be provided once")
            auth = quote(username, safe="") + ":" + quote(password, safe="") + "@"
            uri = urlunsplit(parts._replace(netloc=auth + parts.netloc))
        try:
            result = subprocess.run(
                [
                    binary,
                    "-v",
                    "error",
                    "-rtsp_transport",
                    "tcp",
                    "-timeout",
                    "8000000",
                    "-analyzeduration",
                    "1000000",
                    "-probesize",
                    "1000000",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=codec_name,width,height,r_frame_rate,avg_frame_rate",
                    "-of",
                    "json",
                    uri,
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=12,
            )
            streams = json.loads(result.stdout).get("streams", [])
            if len(streams) != 1:
                raise ValueError("RTSP source must expose a video stream")
            stream = streams[0]
            width, height = int(stream["width"]), int(stream["height"])
            if width <= 0 or height <= 0 or not stream.get("codec_name"):
                raise ValueError("RTSP source has incomplete video metadata")
            fps = 0.0
            for key in ("r_frame_rate", "avg_frame_rate"):
                try:
                    fps = float(Fraction(stream.get(key, "0/1")))
                except (ValueError, ZeroDivisionError):
                    continue
                if fps > 0:
                    break
            return MediaFileInfo(
                video_codec=stream["codec_name"],
                video_fps=fps,
                video_resolution=(width, height),
            )
        except (subprocess.SubprocessError, json.JSONDecodeError, KeyError, TypeError):
            raise ValueError(
                "Could not probe the RTSP video stream with FFprobe"
            ) from None

    @staticmethod
    async def get_info_async(uri_or_file: str, username="", password=""):
        return await asyncio.get_event_loop().run_in_executor(
            _media_info_executor,
            MediaFileInfo.get_info,
            uri_or_file,
            username,
            password,
        )
