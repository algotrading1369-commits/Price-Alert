import os
import threading
import time

from .db import Database
from .events import EventBus
from .indicators import bollinger, rsi, sma
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
    "above": {"label": "Precio por encima de", "value_label": "Precio objetivo"},
    "below": {"label": "Precio por debajo de", "value_label": "Precio objetivo"},
    "change_up_1h": {"label": "Sube un % en 1h", "value_label": "% de cambio"},
    "change_down_1h": {"label": "Baja un % en 1h", "value_label": "% de cambio"},
    "change_up_24h": {"label": "Sube un % en 24h", "value_label": "% de cambio"},
    "change_down_24h": {"label": "Baja un % en 24h", "value_label": "% de cambio"},
    "rsi_below": {"label": "RSI por debajo de (sobreventa)", "value_label": "Nivel RSI (0-100)"},
    "rsi_above": {"label": "RSI por encima de (sobrecompra)", "value_label": "Nivel RSI (0-100)"},
    "ma_cross_up": {"label": "Cruce de medias al alza (24h/72h)", "value_label": None},
    "ma_cross_down": {"label": "Cruce de medias a la baja (24h/72h)", "value_label": None},
    "bollinger_above": {"label": "Rompe banda de Bollinger superior", "value_label": None},
    "bollinger_below": {"label": "Rompe banda de Bollinger inferior", "value_label": None},
    "volume_spike": {"label": "Volumen ≥ X veces la media horaria", "value_label": "Multiplicador (ej. 2 = doble)"},
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
    if kind == "rsi_below":
        return f"RSI ≤ {value:g}"
    if kind == "rsi_above":
        return f"RSI ≥ {value:g}"
    if kind == "ma_cross_up":
        return "cruce de medias al alza (24h/72h)"
    if kind == "ma_cross_down":
        return "cruce de medias a la baja (24h/72h)"
    if kind == "bollinger_above":
        return "precio rompe banda de Bollinger superior"
    if kind == "bollinger_below":
        return "precio rompe banda de Bollinger inferior"
    if kind == "volume_spike":
        return f"volumen ≥ {value:g}× media horaria"
    return f"{kind} {value:g}"


class Engine:
    def __init__(self, db: Database, bus: EventBus, notifier: TelegramNotifier):
        self.db = db
        self.bus = bus
        self.notifier = notifier
        self.mock = os.environ.get("MOCK", "0") == "1"
        self.market = MockMarket() if self.mock else BinanceClient()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread = None
        self._ticks = 0
        self._errors = 0
        self._consecutive_errors = 0
        self._last_tick = 0.0
        self._started_at = 0.0
        self._ind_cache = {}
        self._vol_cache = {}
        self._vol_lock = threading.Lock()

    def poll_interval(self):
        default = 3 if self.mock else 15
        value = self.db.get_setting("poll_seconds", default)
        try:
            return max(5, int(value)) if not self.mock else max(2, int(value))
        except (TypeError, ValueError):
            return default

    def backfill(self, symbol):
        try:
            for point in self.market.klines(symbol, interval="1h", limit=336):
                self.db.add_price(symbol, point["ts"], point["price"], 0, point.get("volume", 0.0))
            return True
        except Exception:
            return False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        if not self.db.assets():
            for symbol, name in DEFAULT_ASSETS:
                self.db.add_asset(symbol, name)
        for asset in self.db.assets():
            if len(self.db.hourly_closes(asset["symbol"], 24 * 15)) < 100:
                self.backfill(asset["symbol"])
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
            self.db.add_price(symbol, now, info["price"], info["change_24h"], info.get("volume", 0.0))
        self.bus.publish("prices", data={s: {"price": i["price"], "change_24h": i["change_24h"]} for s, i in data.items()})
        self._ticks += 1
        self._last_tick = now
        self._ind_cache = {}
        for rule in self.db.rules(enabled_only=True):
            info = data.get(rule["symbol"])
            if not info:
                continue
            self._evaluate(rule, info, now)
        self._maybe_digest(now)
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
            if past is None:
                return
            change = (price - past) / past * 100.0
            triggered = change >= value if kind == "change_up_1h" else change <= -abs(value)
            detail = f"cambio 1h {change:+.2f}%"
        elif kind in ("change_up_24h", "change_down_24h"):
            change = info["change_24h"]
            triggered = change >= value if kind == "change_up_24h" else change <= -abs(value)
            detail = f"cambio 24h {change:+.2f}%"
        elif kind in ("rsi_below", "rsi_above", "ma_cross_up", "ma_cross_down", "bollinger_above", "bollinger_below"):
            result = self._eval_indicator(rule["symbol"], kind, rule["value"])
            if result is None:
                return
            triggered, detail = result
        elif kind == "volume_spike":
            result = self._eval_volume(rule["symbol"], value)
            if result is None:
                return
            triggered, detail = result
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

    def _hourly_closes(self, symbol, hours):
        if symbol not in self._ind_cache:
            self._ind_cache[symbol] = [p["price"] for p in self.db.hourly_closes(symbol, hours)]
        return self._ind_cache[symbol]

    def _eval_indicator(self, symbol, kind, value):
        if kind in ("rsi_below", "rsi_above"):
            closes = self._hourly_closes(symbol, 40)
            if len(closes) < 16:
                return None
            r = rsi(closes, 14)
            detail = f"RSI(14h) {r:.1f}"
            triggered = r <= value if kind == "rsi_below" else r >= value
        elif kind in ("ma_cross_up", "ma_cross_down"):
            closes = self._hourly_closes(symbol, 100)
            if len(closes) < 74:
                return None
            fast_prev = sma(closes[:-1], 24)
            slow_prev = sma(closes[:-1], 72)
            fast_now = sma(closes, 24)
            slow_now = sma(closes, 72)
            if None in (fast_prev, slow_prev, fast_now, slow_now):
                return None
            crossed_up = fast_prev <= slow_prev and fast_now > slow_now
            crossed_down = fast_prev >= slow_prev and fast_now < slow_now
            triggered = crossed_up if kind == "ma_cross_up" else crossed_down
            detail = f"MA24 {fast_now:.6g} vs MA72 {slow_now:.6g}"
        elif kind in ("bollinger_above", "bollinger_below"):
            closes = self._hourly_closes(symbol, 40)
            if len(closes) < 21:
                return None
            lower, mid, upper = bollinger(closes, 20, 2.0)
            price = closes[-1]
            triggered = price > upper if kind == "bollinger_above" else price < lower
            detail = f"precio {price:.6g} vs bandas [{lower:.6g}, {upper:.6g}]"
        else:
            return None
        return (triggered, detail)

    def _eval_volume(self, symbol, value):
        now = time.time()
        with self._vol_lock:
            cached = self._vol_cache.get(symbol)
            if not cached or now - cached[0] > 300:
                try:
                    candles = self.market.klines(symbol, interval="1h", limit=26)
                except MarketError:
                    return None
                self._vol_cache[symbol] = (now, candles)
            else:
                candles = cached[1]
        if len(candles) < 25:
            return None
        last_closed = candles[-2]["volume"]
        avg = sum(c["volume"] for c in candles[-26:-2]) / 24
        if avg <= 0:
            return None
        ratio = last_closed / avg
        return (ratio >= value, f"volumen horario {ratio:.1f}× la media")

    def _maybe_digest(self, now):
        cfg = self.db.get_setting("digest", {"enabled": True, "hour": 9})
        if not cfg.get("enabled"):
            return
        lt = time.localtime(now)
        if lt.tm_hour < cfg.get("hour", 9):
            return
        today = time.strftime("%Y-%m-%d", lt)
        if self.db.get_setting("last_digest") == today:
            return
        self.db.set_setting("last_digest", today)
        self.send_digest()

    def send_digest(self):
        now = time.time()
        lines = ["📊 <b>Resumen diario — Crypto Monitor</b>", ""]
        for asset in self.db.assets():
            symbol = asset["symbol"]
            latest = self.db.latest_price(symbol)
            if not latest:
                continue
            closes = self.db.hourly_closes(symbol, 24 * 8)
            ch7d = ""
            if closes and now - closes[0]["bucket"] > 24 * 6 * 3600:
                old = closes[0]["price"]
                ch7d = f", {(latest['price'] - old) / old * 100:+.1f}% 7d"
            lines.append(f"• <b>{symbol}</b>: {latest['price']:g} ({latest['change_24h']:+.2f}% 24h{ch7d})")
        alerts = [a for a in self.db.recent_alerts(100) if a["ts"] > now - 86400]
        lines.append("")
        if alerts:
            lines.append(f"🔔 {len(alerts)} alertas en las últimas 24h:")
            for a in alerts[-3:]:
                lines.append(f"  - {a['message']}")
        else:
            lines.append("Sin alertas en las últimas 24h ✓")
        message = "\n".join(lines)
        self.db.add_log("info", "Resumen diario generado")
        self.bus.publish("digest", message=message)
        return self.notifier.send(message)
