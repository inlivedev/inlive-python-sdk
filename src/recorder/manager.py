"""
recorder.manager

This module provides the RecorderManager class, which manages multiple RoomRecorder instances for recording media streams in different rooms. It handles the lifecycle of recorders, including creation, deletion, and graceful shutdown on system signals. The manager integrates with a thread pool and supports asynchronous operations for starting and stopping recordings.

Classes:
    RecorderManager: Manages RoomRecorder instances, handles signal-based shutdown, and provides methods to create, retrieve, and delete recorders.

Usage Example:
    from recorder.manager import RecorderManager
    manager = RecorderManager()
    # Use manager.create_recorder(...) to start recording
"""

import asyncio
import logging
import signal

from threadpool.threadpool import ThreadPoolManager

from .recorder import RoomRecorder

logger = logging.getLogger(__name__)


class RecorderManager:
    def __init__(self):
        self.recorders: dict[str, RoomRecorder] = {}

        try:
            loop = asyncio.get_running_loop()
            if loop is not None:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(
                        sig,
                        lambda: asyncio.create_task(self.shutdown_signal()),
                    )
        except RuntimeError:
            pass

    async def shutdown_signal(self):
        await self.end()

    def _on_recorder_ended(self, room_id: str):
        self.delete_recorder(room_id)

    async def create_recorder(
        self,
        threadpool: ThreadPoolManager,
        token: str,
        room_id: str,
        name: str,
        dirpath: str = "recordings",
    ) -> RoomRecorder | None:
        try:
            recorder = RoomRecorder(
                threadpool=threadpool,
                dirpath=dirpath,
                room_id=room_id,
                token=token,
                name=name,
            )

            await recorder.register()

            self.recorders[room_id] = recorder
            logger.debug(f"Recorder for room {room_id} created")

            @recorder.on("ended")
            def on_recorder_ended():
                self._on_recorder_ended(room_id)

            return recorder
        except Exception as e:
            logger.error(f"Failed to create recorder: {e}")
            return None

    def get_recorder(self, room_id: str) -> RoomRecorder | None:
        return self.recorders.get(room_id)

    def get_recorders(self):
        return self.recorders

    def delete_recorder(self, room_id: str):
        return self.recorders.pop(room_id, None)

    async def end(self):
        while len(self.recorders) > 0:
            room_id, recorder = self.recorders.popitem()
            if recorder is not None:
                await recorder.close()

            if room_id in self.recorders:
                del self.recorders[room_id]
