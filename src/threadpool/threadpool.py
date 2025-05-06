# threadpool_manager.py
import asyncio
import concurrent.futures
import os


class ThreadPoolManager:
    def __init__(self, max_workers=None):
        if max_workers is None:
            # Default to CPU cores * 2 if max_workers is not specified
            max_workers = os.cpu_count()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self.loop = asyncio.get_event_loop()

    def submit_task(self, task, *args, **kwargs):
        """Submits a task to the thread pool."""
        return self.executor.submit(task, *args, **kwargs)

    async def _run_coroutine_in_thread(self, coroutine, *args, **kwargs):
        """Runs a coroutine in a separate thread."""

        async def wrapper():
            return await coroutine(*args, **kwargs)

        return await self.loop.run_in_executor(self.executor, asyncio.run, wrapper())

    def submit_atask(self, coroutine, *args, **kwargs):
        """Submits an async task to be run in a separate thread."""
        return asyncio.create_task(
            self._run_coroutine_in_thread(coroutine, *args, **kwargs)
        )

    async def run_coroutine(self, coroutine):
        """Runs a coroutine and returns its result."""
        return await coroutine

    def shutdown(self, wait=True):
        """Shuts down the thread pool."""
        self.executor.shutdown(wait=wait)
