import sys
import os
import pytest
from unittest.mock import MagicMock, patch
from aiortc import MediaStreamTrack
import asyncio

from rtc.rtc import RTC
from rtc.api import create_room, generate_access_token
import uuid


class DummyRTC(RTC):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handled_tracks = []

    def _handle_track(self, track):
        """
        Handles the received track by appending it to the handled_tracks list.

        This method should be called whenever a new track is received and needs to be processed.
        """
        self.handled_tracks.append(track)

    async def _on_close(self):
        pass


def test_rtc_init():
    threadpool = MagicMock()
    room_id = "room1"
    token = "token123"
    name = "testuser"
    room_config = {}
    rtc = DummyRTC(threadpool, room_id, token, name, room_config)
    assert rtc.room_id == room_id
    assert rtc.token == token
    assert rtc.name == name
    assert rtc.threadpool == threadpool
    assert rtc.client_id is None
    assert isinstance(rtc.tracks_map, dict)
    assert rtc._track_count == 0


def test_rtc_handle_track():
    threadpool = MagicMock()
    room_id = "room1"
    token = "token123"
    name = "testuser"
    room_config = {}
    rtc = DummyRTC(threadpool, room_id, token, name, room_config)

    # Simulate receiving a track
    track = MagicMock(spec=MediaStreamTrack)
    rtc._handle_track(track)
    assert track in rtc.handled_tracks

    # Simulate connection creation
    assert rtc.pc is not None
    assert rtc.pc.connectionState in (
        "new",
        "closed",
        "connecting",
        "connected",
        "disconnected",
        "failed",
    )


@pytest.mark.asyncio
async def test_rtc_connect_and_connected():
    threadpool = MagicMock()
    room_id = str(uuid.uuid4())
    # Always generate a fresh access token
    access_token, refresh_token, err = generate_access_token()
    assert access_token, "No access token generated"
    assert refresh_token, "No refresh token generated"
    name = "testuser"
    room_config = {}
    rtc = DummyRTC(threadpool, room_id, access_token, name, room_config)

    # Ensure the room is created before registering
    room = create_room(room_id, access_token)
    assert room is not None, "Room creation failed"

    await rtc.register()
    await rtc.connect()

    # Listen for connectionstatechange event
    states = []

    def on_state_change(state):
        states.append(state)

    rtc.on("connectionstatechange", on_state_change)

    async def wait_for_connected():
        while "connected" not in states:
            await asyncio.sleep(0.1)

    await asyncio.wait_for(wait_for_connected(), timeout=5)
    assert "connected" in states, "Connection state did not reach 'connected'"
