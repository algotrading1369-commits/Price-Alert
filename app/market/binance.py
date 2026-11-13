import json
import os
import time

import requests

API_URL = os.environ.get("BINANCE_API_URL", "https://api.binance.com")


class MarketError(Exception):
    pass


class BinanceClient:
    def __init__(self):
        self._last_call = 0.0

    def _throttle(self):
        wait = 0.25 - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _get(self, path, params=None):
        self._throttle()
        try:
            resp = requests.get(API_URL + path, params=params, timeout=10)
        except requests.RequestException as exc:
            raise MarketError(f"Sin conexión con el mercado: {exc}") from exc
        if resp.status_code == 400:
            raise MarketError("Símbolo no válido en el mercado")
        if resp.status_code in (418, 429):
            raise MarketError("El mercado está limitando las peticiones (rate limit)")
        if resp.status_code != 200:
            raise MarketError(f"El mercado respondió {resp.status_code}")
        return resp.json()

    def ticker_batch(self, symbols):
        data = self._get("/api/v3/ticker/24hr", {"symbols": json.dumps(symbols)})
        result = {}
        for item in data:
            result[item["symbol"]] = {
                "price": float(item["lastPrice"]),
                "change_24h": float(item["priceChangePercent"]),
                "high": float(item["highPrice"]),
                "low": float(item["lowPrice"]),
                "volume": float(item["quoteVolume"]),
            }
        return result

    def klines(self, symbol, interval="15m", limit=96):
        data = self._get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        return [{"ts": k[0] / 1000.0, "price": float(k[4]), "volume": float(k[7])} for k in data]
