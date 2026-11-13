import json
import queue
import threading
import time


class EventBus:
    def __init__(self, max_history=500):
        self._lock = threading.Lock()
        self._subscribers = []
        self._history = []
        self._max_history = max_history

    def subscribe(self):
        q = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, kind, **data):
        event = {"kind": kind, "ts": time.time(), **data}
        with self._lock:
            self._history.append(event)
            self._history = self._history[-self._max_history:]
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(json.dumps(event, default=str))
            except queue.Full:
                pass

    def history(self, since=0):
        with self._lock:
            return [e for e in self._history if e["ts"] > since]
