import threading
import queue
import sys
from datetime import datetime

class AsyncLogger:
    def __init__(self):
        self.queue = queue.Queue()
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()

    def _worker(self):
        while True:
            msg = self.queue.get()
            if msg is None:
                break
            # Print to stdout with timestamp
            sys.stdout.write(msg + '\n')
            sys.stdout.flush()
            self.queue.task_done()

    def log(self, message):
        """Queue a message for logging."""
        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        formatted_msg = f"[{timestamp}] {message}"
        self.queue.put(formatted_msg)

    def info(self, message):
        self.log(f"INFO: {message}")

    def warn(self, message):
        self.log(f"WARN: {message}")

    def error(self, message):
        self.log(f"ERROR: {message}")

    def stop(self):
        self.queue.put(None)
        self.worker_thread.join()

# Global instance
logger = AsyncLogger()
