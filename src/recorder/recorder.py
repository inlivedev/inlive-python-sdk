"""
recorder.py
-----------
This module provides classes for recording audio and video tracks from WebRTC streams using aiortc and PyAV. It includes Recorder for individual track recording and RoomRecorder for managing multiple recorders in a room context.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from aiortc import MediaStreamTrack, sdp
from aiortc.contrib.media import MediaRelay, MediaRecorder
from aiortc.mediastreams import (
    MediaStreamError,
)
from av.video.frame import VideoFrame

import logger.logger as log
from threadpool.threadpool import ThreadPoolManager
from rtc.rtc import RTC

logger = log.get_logger(__name__)

config = {
    "iceServers": [
        {
            "urls": [
                "turn:turn.inlive.app:3478?transport=udp",
                "turn:turn.inlive.app:3479?transport=tcp",
            ],
            "username": "inlive",
            "credential": "inlivesdkturn",
        },
    ],
}


class Recorder:
    """
    Recorder for audio and video tracks using aiortc and PyAV.
    """

    def __init__(
        self,
        threadpool: ThreadPoolManager,
        sid: str,
        path: str,
        format=None,
        options=None,
    ):
        """
        Initialize a Recorder instance.

        Args:
            threadpool (ThreadPoolManager): Thread pool manager for async tasks.
            sid (str): Stream or session identifier.
            path (str): Output file path.
            format: Optional file format for PyAV.
            options: Optional encoding options for PyAV.
        """
        self.threadpool = threadpool
        self.sid = sid
        self._path = path
        self.audio_track = None
        self.video_track = None

        self._audio_stream = None
        self._video_stream = None

        self._audio_task = None
        self._video_task = None

        self._recorder = MediaRecorder(path)
        self._is_started = False
        self._is_stopped = False

    def add_audio_track(self, track: MediaStreamTrack):
        """
        Add an audio track to the recorder.

        Args:
            track (MediaStreamTrack): The audio track to record.
        """
        self.audio_track = track
        self._audio_stream = self._recorder.addTrack(track)

    def add_video_track(self, track: MediaStreamTrack):
        """
        Add a video track to the recorder.

        Args:
            track (MediaStreamTrack): The video track to record.
        """
        self.video_track = track
        self._video_stream = self._recorder.addTrack(track)

    @property
    def is_started(self):
        """
        bool: Whether the recorder has started.
        """
        return self._is_started

    @property
    def is_stopped(self):
        """
        bool: Whether the recorder has stopped.
        """
        return self._is_stopped

    async def start(self):
        """
        Start recording audio and/or video tracks.
        """
        if self.audio_track is None and self.video_track is None:
            raise Exception("No tracks to record")

        await self._recorder.start()

        self._is_started = True
        self._is_stopped = False
        logger.debug("Recorder started at %s", self._path)

    async def stop(self):
        """
        Stop recording and close the output file.
        """
        if self._is_started and not self._is_stopped:

            if self._recorder:
                await self._recorder.stop()

            self._is_stopped = True
            self._is_started = False
            logger.debug("Recorder stopped at %s", self._path)


class RoomRecorder(RTC):
    """
    RTC subclass for managing multiple recorders in a room context.
    Handles track subscription, relay, and lifecycle of recorders.
    """

    def __init__(
        self,
        threadpool: ThreadPoolManager,
        dirpath: str,
        room_id: str,
        token: str,
        name: str,
        room_config: dict | None = None,
    ):
        """
        Initialize a RoomRecorder instance.

        Args:
            threadpool (ThreadPoolManager): Thread pool manager for async tasks.
            dirpath (str): Directory path for output files.
            room_id (str): Room identifier.
            token (str): Access token for authentication.
            name (str): Name of the client/agent.
            room_config (dict | None): Optional room configuration.
        """
        super().__init__(
            threadpool=threadpool,
            room_id=room_id,
            token=token,
            name=name,
            room_config=room_config,
        )

        self._dirpath = dirpath
        self._relay = MediaRelay()

        self._recorders: dict[Recorder] = {}

        self.pc.addTransceiver("audio")
        self.pc.addTransceiver("video")

    async def start(self):
        """
        Start all recorders in the room.
        """
        for recorder in self._recorders.values():
            await recorder.start()

    async def stop(self):
        """
        Stop all recorders in the room.
        """
        for recorder in self._recorders.values():
            await recorder.stop()

    def get_track_msid(self, track_id: str) -> str | None:
        """
        Get the tracks map from the remote SDP

        Returns:
            dict: A dictionary mapping track IDs to their corresponding msid.
        """
        remoteSDP = self.pc.remoteDescription
        if remoteSDP is None:
            return {}

        tracks_map = {}
        sessionDescription = sdp.SessionDescription.parse(remoteSDP.sdp)
        for media in sessionDescription.media:
            if media.msid:
                bits = media.msid.split(" ")
                if len(bits) == 2:
                    track_id = bits[1]
                    msid = bits[0]
                    tracks_map[track_id] = msid

        return tracks_map[track_id] if track_id in tracks_map else None

    def _handle_track(self, track: MediaStreamTrack):
        """
        Handle a new incoming track, create or update a recorder, and subscribe to events.

        Args:
            track (MediaStreamTrack): The received media track.
        """
        # get the latest recorder and create a new one if there is none

        msid = self.get_track_msid(track.id)

        if msid is None:
            logger.error(f"Track {track.id} not found in tracks map")
            return

        relay = self._relay.subscribe(track)

        recorder = self._recorders.get(msid)
        if recorder is None:
            path = f"{self._dirpath}/{msid}.mp4"
            recorder = Recorder(self.threadpool, msid, path)
            self._recorders[msid] = recorder

        # add the track to the recorder
        if track.kind == "audio":
            recorder.add_audio_track(relay)
        elif track.kind == "video":
            recorder.add_video_track(relay)

        self.emit("recorders_changed", self._recorders)

        @track.on("ended")
        async def on_ended():
            logger.debug("Track ended")
            if recorder.is_started and not recorder.is_stopped:
                await recorder.stop()
                self.emit("recording_stopped", recorder.sid)

    async def _on_close(self):
        """
        Stop all recorders and clean up when the room is closed.
        """
        logger.debug(f"Recorder {self.name} closed")
        for recorder in self._recorders.values():
            if recorder.is_started and not recorder.is_stopped:
                await recorder.stop()
