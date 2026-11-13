import os
import threading
import time

from .db import Database
from .events import EventBus
from .market.binance import BinanceClient, MarketError
from .market.mock import MockMarket
from .notify import TelegramNotifier

DEFAULT_ASSETS = [
    ("BTCUSDT", "Bitcoin"),
    ("ETHUSDT", "Ethereum"),
    ("SOLUSDT", "Solana"),
    ("BNBUSDT", "BNB"),
    ("XRPUSDT", "XRP"),
]

RULE_KINDS = {
    "above": "Precio por encima de",
    "below": "Precio por debajo de",
    "change_up_1h": "Sube un % en 1h",
    "change_down_1h": "Baja un % en 1h",
    "change_up_24h": "Sube un % en 24h",
    "change_down_24h": "Baja un % en 24h",
}


def describe_rule(kind, value):
    if kind == "above":
        return f"precio ≥ {value:g}"
    if kind == "below":
        return f"precio ≤ {value:g}"
    if kind == "change_up_1h":
        return f"subida ≥ {value:g}% en 1h"
    if kind == "change_down_1h":
        return f"caída ≥ {value:g}% en 1h"
    if kind == "change_up_24h":
        return f"subida ≥ {value:g}% en 24h"
    if kind == "change_down_24h":
        return f"caída ≥ {value:g}% en 24h"
    return f"{kind} {value:g}"


class Engine:
    def __init__(self, db: Database, bus: EventBus, notifier: TelegramNotifier):
        self.db = db
        self.bus = bus
        self.notifier = notifier
        self.mock = os.environ.get("MOCK", "0") == "1"
        self.market = MockMarket() if self.mock else BinanceClient()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread = None
        self._ticks = 0
        self._errors = 0
        self._consecutive_errors = 0
        self._last_tick = 0.0
        self._started_at = 0.0

    def poll_interval(self):
        default = 3 if self.mock else 15
        value = self.db.get_setting("poll_seconds", default)
        try:
            return max(5, int(value)) if not self.mock else max(2, int(value))
        except (TypeError, ValueError):
            return default

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        if not self.db.assets():
            for symbol, name in DEFAULT_ASSETS:
                self.db.add_asset(symbol, name)
        self._stop.clear()
        self._paused.clear()
        self._started_at = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="monitor-engine")
        self._thread.start()
        mode = "demo (precios simulados)" if self.mock else "real (Binance)"
        self.db.add_log("info", f"Monitor iniciado en modo {mode}")

    def stop(self):
        self._stop.set()

    def pause(self, paused):
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    @property
    def paused(self):
        return self._paused.is_set()

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self):
        return {
            "mock": self.mock,
            "running": self.running,
            "paused": self.paused,
            "poll_seconds": self.poll_interval(),
            "ticks": self._ticks,
            "errors": self._errors,
            "last_tick": self._last_tick,
            "started_at": self._started_at,
        }

    def _loop(self):
        while not self._stop.is_set():
            interval = self.poll_interval()
            if self._paused.is_set():
                self._stop.wait(2)
                continue
            start = time.time()
            try:
                self._tick()
                self._consecutive_errors = 0
            except MarketError as exc:
                self._errors += 1
                self._consecutive_errors += 1
                if self._consecutive_errors <= 2 or self._consecutive_errors % 20 == 0:
                    self.db.add_log("error", str(exc))
            except Exception:
                self._errors += 1
                self._consecutive_errors += 1
                if self._consecutive_errors <= 2 or self._consecutive_errors % 20 == 0:
                    self.db.add_log("error", "Error inesperado en el ciclo de precios")
            elapsed = time.time() - start
            self._stop.wait(max(0.5, interval - elapsed))

    def _tick(self):
        assets = self.db.assets()
        if not assets:
            return
        symbols = [a["symbol"] for a in assets]
        data = self.market.ticker_batch(symbols)
        now = time.time()
        for symbol, info in data.items():
            self.db.add_price(symbol, now, info["price"], info["change_24h"])
        self.bus.publish("prices", data={s: {"price": i["price"], "change_24h": i["change_24h"]} for s, i in data.items()})
        self._ticks += 1
        self._last_tick = now
        for rule in self.db.rules(enabled_only=True):
            info = data.get(rule["symbol"])
            if not info:
                continue
            self._evaluate(rule, info, now)
        if self._ticks % 500 == 0:
            self.db.prune_prices(3)

    def _evaluate(self, rule, info, now):
        price = info["price"]
        kind = rule["kind"]
        value = rule["value"]
        triggered = False
        detail = ""
        if kind == "above":
            triggered = price >= value
            detail = f"precio {price:g} ≥ {value:g}"
        elif kind == "below":
            triggered = price <= value
            detail = f"precio {price:g} ≤ {value:g}"
        elif kind in ("change_up_1h", "change_down_1h"):
            past = self.db.price_before(rule["symbol"], now - 3600)
            if past is None or now - 3600 < 0:
                return
            change = (price - past) / past * 100.0
            triggered = change >= value if kind == "change_up_1h" else change <= -abs(value)
            detail = f"cambio 1h {change:+.2f}%"
        elif kind in ("change_up_24h", "change_down_24h"):
            change = info["change_24h"]
            triggered = change >= value if kind == "change_up_24h" else change <= -abs(value)
            detail = f"cambio 24h {change:+.2f}%"
        if not triggered:
            return
        if now - rule["last_triggered"] < rule["cooldown_min"] * 60:
            return
        self.db.set_rule_triggered(rule["id"], now)
        message = f"🚨 <b>{rule['symbol']}</b>: {describe_rule(kind, value)} — {detail}"
        self.db.add_alert(rule["id"], rule["symbol"], kind, price, message)
        self.db.add_log("alert", f"{rule['symbol']}: {describe_rule(kind, value)} — {detail}")
        self.bus.publish("alert", symbol=rule["symbol"], message=message, price=price)
        self.notifier.send(f"🚨 {rule['symbol']}: {describe_rule(kind, value)}\n{detail}")
