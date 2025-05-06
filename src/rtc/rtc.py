"""
rtc.py
-------
This module provides the RTC class for managing WebRTC peer connections, signaling, and event handling for the inLive platform. It handles negotiation, ICE candidates, track management, and connection lifecycle events using aiortc and async event streams.
"""

import asyncio
import json
import os
from abc import ABCMeta, abstractmethod
from typing import Optional, Tuple

from aioice import Candidate
from aiortc import (
    MediaStreamTrack,
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
    sdp,
)
from aiortc.rtcicetransport import candidate_from_aioice
from httpx import AsyncClient, Response, Timeout
from pyee.asyncio import AsyncIOEventEmitter

import logger.logger as log
from threadpool.threadpool import ThreadPoolManager

from .api import URL_PREFIX

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


class RTC(AsyncIOEventEmitter, metaclass=ABCMeta):
    """
    Abstract base class for managing a WebRTC peer connection and signaling with the inLive server.
    Handles negotiation, ICE candidates, track management, and connection lifecycle events.
    """

    def __init__(
        self,
        threadpool: ThreadPoolManager,
        room_id: str,
        token: str,
        name: str,
        room_config: dict | None,
    ):
        """
        Initialize the RTC connection and configuration.

        Args:
            threadpool (ThreadPoolManager): Thread pool manager for async tasks.
            room_id (str): Room identifier.
            token (str): Access token for authentication.
            name (str): Name of the client/agent.
            room_config (dict | None): Optional room configuration.
        """
        super().__init__()
        self.threadpool = threadpool
        self.room_id = room_id
        self.client_id = None
        self.name = name
        self.token = token
        self._listened = False
        self.tracks_map = {}
        self._track_count = 0

        if isinstance(room_config, dict):
            if "iceServers" in room_config:
                config["iceServers"] = room_config["iceServers"]

            if "origin" in room_config:
                self.origin = room_config["origin"]

            if "version" in room_config:
                self.version = room_config["version"]

            if "api_key" in room_config:
                self.api_key = room_config["api_key"]

            if isinstance(room_config, dict):
                if "iceServers" in room_config:
                    config["iceServers"] = room_config["iceServers"]

                if "origin" in room_config:
                    self.origin = room_config["origin"]

                if "version" in room_config:
                    self.version = room_config["version"]

                if "api_key" in room_config:
                    self.api_key = room_config["api_key"]

        configuration = RTCConfiguration(
            iceServers=[
                RTCIceServer(
                    urls=["turn:turn.inlive.app:3478"],
                    username="inlive",
                    credential="inlivesdkturn",
                ),
            ],
        )

        hub_origin = os.getenv("HUB_ORIGIN")
        if hub_origin is not None and hub_origin.startswith("http://localhost"):
            self.pc = RTCPeerConnection()
        else:
            self.pc = RTCPeerConnection(configuration=configuration)

        @self.pc.on("track")
        async def on_track(track: MediaStreamTrack):
            logger.debug("Track %s received" % track.kind)
            try:
                self._handle_track(track)
                self._track_count += 1
                logger.debug("Track %d active" % self._track_count)
            except Exception as e:
                logger.exception("Failed to handle track: %s" % e)

            @track.on("ended")
            async def on_ended():
                if self._track_count > 0:
                    self._track_count -= 1
                    logger.debug("Track %d active" % self._track_count)
                if self._track_count == 0:
                    await self.close()
                    logger.debug("All tracks ended")

    @abstractmethod
    def _handle_track(self, track: MediaStreamTrack):
        """
        Abstract method to handle a received media track.

        Args:
            track (MediaStreamTrack): The received media track.
        """
        pass

    async def _negotiate(self, offersdp: str | None = None):
        """
        Handle SDP negotiation with the server, setting remote and local descriptions.

        Args:
            offersdp (str | None): Optional SDP offer from the server.
        Returns:
            bool: False if negotiation fails, otherwise None.
        """

        offerjson = json.loads(offersdp)

        try:
            async with AsyncClient() as fetch:
                offer = None

                if offersdp is None:
                    offer = await self.pc.createOffer()
                else:
                    offer = RTCSessionDescription(sdp=offerjson["sdp"], type="offer")

                await self.pc.setRemoteDescription(offer)

                answer = await self.pc.createAnswer()
                await self.pc.setLocalDescription(answer)
                url = f"{URL_PREFIX}/rooms/{self.room_id}/negotiate/{self.client_id}"
                resp = await fetch.put(
                    url,
                    json={"type": "answer", "sdp": self.pc.localDescription.sdp},
                )

                if resp.status_code >= 200 and resp.status_code < 300:
                    logger.debug("Answered negotiation")
                else:
                    logger.error(
                        "Failed to answer renegotiation, status code: %s"
                        % resp.status_code,
                    )
                    raise Exception(
                        "Failed to answer renegotiation, status code: %s"
                        % resp.status_code,
                    )
        except Exception as e:
            logger.error("Failed to answer renegotiation: %s" % e)
            return False

    @abstractmethod
    async def _on_close(self):
        """
        Abstract method called when the RTC connection is closed.
        """
        pass

    async def close(self):
        """
        Close the RTC connection and clean up resources.
        """
        client_id = self.client_id

        if self.pc is not None:
            await self.pc.close()
            self.pc = None

        self.on_ice_candidate = None

        self.ice_candidate = None

        await self._on_close()

        self.emit("ended")

        self.remove_all_listeners()

        logger.debug("Agent closed %s" % client_id)

    async def _event_handler(self, response: Response):
        """
        Handle incoming server-sent events for signaling and track management.

        Args:
            response (Response): The HTTPX response streaming events.
        """
        event = None
        data = None

        logger.debug("Event stream started")

        async for line in response.aiter_lines():
            if line == "":
                continue

            if line.startswith("event:"):
                event = line.replace("event:", "").strip()
                data = None
            elif line.startswith("data:"):
                data = line.replace("data:", "").strip()
            else:
                event = None
                data = None
                continue

            if self.pc.connectionState == "closed":
                break

            if data is not None and event != "ping":
                logger.debug("Event: %s" % event)

                if event == "left":
                    await self.close()
                    break

                if event == "offer":
                    await self._negotiate(data)
                elif event == "candidate":
                    candidate = json.loads(data)
                    candidatestring = candidate["candidate"].replace("candidate:", "")
                    candidateinit = Candidate.from_sdp(candidatestring)

                    icecandidate = candidate_from_aioice(candidateinit)
                    icecandidate.sdpMid = candidate["sdpMid"]
                    icecandidate.sdpMLineIndex = candidate["sdpMLineIndex"]

                    await self.pc.addIceCandidate(icecandidate)

                elif event == "tracks_added":
                    tracks = json.loads(data)
                    logger.debug("Tracks added")
                    sources = []
                    for id, track in tracks["tracks"].items():
                        sources.append({"track_id": id, "source": "media"})

                    headers = {
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                    }
                    async with AsyncClient() as fetch:
                        resp = await fetch.put(
                            f"{URL_PREFIX}/rooms/{self.room_id}/settracksources/{self.client_id}",
                            json=sources,
                            headers=headers,
                        )
                        if resp.status_code >= 200 and resp.status_code < 300:
                            logger.debug("set track sources")
                        else:
                            logger.error(
                                "Failed to set track sources, status code: %s"
                                % resp.status_code,
                            )
                            raise Exception(
                                "Failed to set track sources, status code: %s"
                                % resp.status_code,
                            )

                elif event == "tracks_available":
                    logger.debug("Tracks available")

                    tracks = json.loads(data)["tracks"]

                    subs_req = []

                    for track_id, track in tracks.items():
                        self.tracks_map[track_id] = track["client_id"]
                        subs_req.append(
                            {
                                "track_id": track["track_id"],
                                "client_id": track["client_id"],
                            },
                        )

                    headers = {
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                    }

                    async with AsyncClient() as fetch:
                        logger.debug("client state %s" % self.pc.connectionState)
                        resp = await fetch.post(
                            f"{URL_PREFIX}/rooms/{self.room_id}/subscribetracks/{self.client_id}",
                            json=subs_req,
                            headers=headers,
                        )
                        if resp.status_code >= 200 and resp.status_code < 300:
                            logger.debug("Tracks subscribed")
                        else:
                            logger.error(
                                "Failed to subscribe tracks, status code: %s"
                                % resp.status_code,
                            )
                            raise Exception(
                                "Failed to subscribe tracks, status code: %s"
                                % resp.status_code,
                            )
                elif event == "allowed_renegotation":
                    logger.debug("Allowed renegotiation")
                    await self._negotiate()

        logger.debug("Event stream ended")
        self.ready = False

    async def _listen(self):
        """
        Listen for server-sent events for this RTC client.
        """
        self._listened = True

        headers = {
            "Accept": "text/event-stream",
        }
        fetch = AsyncClient()
        url = f"{URL_PREFIX}/rooms/{self.room_id}/events/{self.client_id}"
        timeout = Timeout(None, connect=None, read=60.0)

        while True:
            if self.pc is None or self.pc.connectionState == "closed":
                break
            try:
                async with fetch.stream(
                    method="GET",
                    url=url,
                    headers=headers,
                    timeout=timeout,
                ) as response:
                    await self._event_handler(response)
            except Exception as e:
                logger.error("Event stream error: %s" % e)
                break

        self._listened = False

    async def register(self):
        """
        Register the RTC agent with the inLive server.

        Returns:
            str: The client ID assigned by the server.
        Raises:
            Exception: If registration fails.
        """
        async with AsyncClient() as fetch:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            }
            req = {"name": self.name, "enable_vad": True, "disable_ice_tricket": True}
            resp = await fetch.post(
                f"{URL_PREFIX}/rooms/{self.room_id}/register",
                json=req,
                headers=headers,
            )
            if resp.status_code >= 200 and resp.status_code < 300:
                self.client_id = resp.json()["data"]["client_id"]
                logger.debug("Agent registered: %s" % self.client_id)
                return self.client_id
            # Return error JSON if registration fails
            error_json = resp.json()
            logger.error(f"Failed to register agent: {error_json}")
            raise Exception(f"Failed to register agent: {error_json}")

    async def connect(self):
        """
        Establish the RTC connection and perform signaling with the server.
        """
        try:
            async with AsyncClient() as fetch:
                self.ignore_data_channel = self.pc.createDataChannel("ignore")

                @self.pc.on("negotiationneeded")
                async def on_negotiationneeded():
                    self.emit("negotiationneeded")

                @self.pc.on("iceconnectionstatechange")
                async def on_iceconnectionstatechange():
                    self.emit("iceconnectionstatechange", self.pc.iceConnectionState)
                    logger.info(
                        "ICE connection state is %s" % self.pc.iceConnectionState
                    )

                @self.pc.on("connectionstatechange")
                async def on_connectionstatechange():
                    if self.pc is None:
                        return
                    self.emit("connectionstatechange", self.pc.connectionState)
                    logger.info("Connection state is %s" % self.pc.connectionState)
                    if self.pc.connectionState == "failed":
                        await self.close()

                headers = {
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                }
                resp = await fetch.post(
                    f"{URL_PREFIX}/rooms/{self.room_id}/isallownegotiate/{self.client_id}",
                    headers=headers,
                )
                if resp.status_code > 299:
                    error_json = resp.json()
                    logger.error(
                        f"Failed to check if negotiation is allowed: {error_json}"
                    )
                    raise Exception(
                        f"Failed to check if negotiation is allowed: {error_json}"
                    )
                offer = await self.pc.createOffer()
                await self.pc.setLocalDescription(offer)
                while True:
                    logger.info("icegatheringstate: %s", self.pc.iceGatheringState)
                    if self.pc.iceGatheringState == "complete":
                        break
                resp = await fetch.put(
                    f"{URL_PREFIX}/rooms/{self.room_id}/negotiate/{self.client_id}",
                    headers=headers,
                    json={
                        "type": self.pc.localDescription.type,
                        "sdp": self.pc.localDescription.sdp,
                    },
                )
                if resp.status_code >= 200 and resp.status_code < 300:
                    data = resp.json()
                    if "answer" in data["data"]:
                        answer = RTCSessionDescription(
                            sdp=data["data"]["answer"]["sdp"],
                            type="answer",
                        )
                        try:
                            await self.pc.setRemoteDescription(answer)
                            if self._listened is False:
                                asyncio.run_coroutine_threadsafe(
                                    self._listen(),
                                    asyncio.get_event_loop(),
                                )
                        except Exception as e:
                            logger.error("Failed to set remote description: %s" % e)
                            raise Exception("Failed to set remote description")
                else:
                    error_json = resp.json()
                    logger.error(f"Failed to negotiate: {error_json}")
                    raise Exception(f"Failed to negotiate: {error_json}")
        except Exception as e:
            logger.error("Failed to connect: %s" % e)

    async def get_packet_loss_stats(self):
        """
        Retrieve and log audio packet loss statistics for the RTC connection.
        """
        stats = await self.pc.getStats()
        for report in stats.values():
            if report.type == "inbound-rtp" and report.kind == "audio":
                packets_lost = report.packetsLost
                packets_received = report.packetsReceived
                logger.info(f"Packets lost: {packets_lost}")
                logger.info(f"Packets received: {packets_received}")
                if packets_received > 0:
                    packet_loss_rate = packets_lost / (packets_lost + packets_received)
                    logger.info(f"Packet loss rate: {packet_loss_rate:.2%}")

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
