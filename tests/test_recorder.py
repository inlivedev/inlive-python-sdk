import os
import pytest
import asyncio
import uuid
from aiortc import VideoStreamTrack
import numpy as np
from aiortc.contrib.media import MediaPlayer
import platform

from recorder.manager import RecorderManager
from rtc.api import create_room, generate_access_token
from threadpool.threadpool import ThreadPoolManager
from rtc.rtc import RTC


class TestVideoStreamTrack(VideoStreamTrack):
    kind = "video"

    def __init__(self):
        super().__init__()
        self.counter = 0

    async def recv(self):
        from av import VideoFrame

        pts, time_base = self.counter, 1 / 30
        img = np.zeros((480, 640, 3), np.uint8)
        frame = VideoFrame.from_ndarray(img, format="bgr24")
        frame.pts = pts
        frame.time_base = time_base
        self.counter += 1
        await asyncio.sleep(1 / 30)
        return frame


class PublisherRTC(RTC):
    def _handle_track(self, track):
        pass

    async def _on_close(self):
        pass

    def add_test_tracks(self):
        from aiortc import RTCRtpSender

        # Use synthetic video track for testing instead of MediaPlayer
        options = {"framerate": "30", "video_size": "640x480"}
        # Use webcam or test video/audio file depending on platform
        if platform.system() == "Darwin":
            player = MediaPlayer("default:none", format="avfoundation", options=options)
        elif platform.system() == "Windows":
            player = MediaPlayer(
                "video=Integrated Camera", format="dshow", options=options
            )
        else:
            player = MediaPlayer("/dev/video0", format="v4l2", options=options)

        if player.audio:
            self.pc.addTrack(player.audio)
        if player.video:
            self.pc.addTrack(player.video)


@pytest.mark.asyncio
async def test_room_recorder_with_publisher_and_subscriber():
    threadpool = ThreadPoolManager()
    room_id = str(uuid.uuid4())
    access_token, refresh_token, err = generate_access_token()
    assert access_token, "No access token generated"
    name = "recorder-bot"
    dirpath = "recordings"
    assert os.path.exists(dirpath), f"Directory {dirpath} does not exist"
    room, _ = create_room(room_id, access_token)
    assert room is not None, "Room creation failed"
    manager = RecorderManager()
    # Start publisher first
    publisher = PublisherRTC(threadpool, room_id, access_token, "publisher", {})
    publisher.add_test_tracks()
    await publisher.register()
    await publisher.connect()
    # Now start recorder (subscriber)
    subscriber = await manager.create_recorder(
        threadpool=threadpool,
        token=access_token,
        room_id=room_id,
        name="subscriber-recorder",
        dirpath=dirpath,
    )
    assert subscriber is not None, "Subscriber RoomRecorder creation failed"
    await subscriber.register()
    await subscriber.connect()

    # Wait until both are connected
    async def wait_connected(rtc):
        while True:
            if hasattr(rtc, "pc") and rtc.pc.connectionState == "connected":
                return
            await asyncio.sleep(0.1)

    await asyncio.wait_for(wait_connected(publisher), timeout=10)
    assert publisher.pc.connectionState == "connected", "Publisher not connected"
    await asyncio.wait_for(wait_connected(subscriber), timeout=10)
    assert subscriber.pc.connectionState == "connected", "Subscriber not connected"
    # Let the subscriber start recording
    await asyncio.sleep(5)
    await subscriber.start()
    await asyncio.sleep(20)
    await subscriber.stop()
    await subscriber.close()
    await publisher.close()
    await manager.end()
    await asyncio.sleep(3)
    files = os.listdir(dirpath)
    assert any(f.endswith(".mp4") for f in files), "No recording file created"
