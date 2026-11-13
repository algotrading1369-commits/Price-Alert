import random
import time


class MockMarket:
    BASE = {
        "BTCUSDT": 97250.0,
        "ETHUSDT": 3480.0,
        "SOLUSDT": 212.0,
        "BNBUSDT": 645.0,
        "XRPUSDT": 2.35,
        "ADAUSDT": 0.97,
        "DOGEUSDT": 0.36,
    }

    def __init__(self):
        self._prices = dict(self.BASE)
        self._open24 = {k: v * random.uniform(0.97, 1.03) for k, v in self._prices.items()}

    def ticker_batch(self, symbols):
        result = {}
        for symbol in symbols:
            if symbol not in self._prices:
                self._prices[symbol] = round(random.uniform(0.5, 500.0), 4)
                self._open24[symbol] = self._prices[symbol] * random.uniform(0.97, 1.03)
            drift = random.gauss(0, 0.002)
            if random.random() < 0.02:
                drift += random.choice([-0.02, 0.02])
            self._prices[symbol] = max(self._prices[symbol] * (1 + drift), 0.0001)
            price = self._prices[symbol]
            open24 = self._open24[symbol]
            result[symbol] = {
                "price": price,
                "change_24h": (price - open24) / open24 * 100.0,
                "high": max(price, open24) * random.uniform(1.0, 1.015),
                "low": min(price, open24) * random.uniform(0.985, 1.0),
                "volume": random.uniform(1e6, 5e8),
            }
        return result

    def klines(self, symbol, interval="15m", limit=96):
        price = self._prices.get(symbol, 100.0)
        out = []
        now = time.time()
        for i in range(limit, 0, -1):
            price = max(price * (1 + random.gauss(0, 0.004)), 0.0001)
            out.append({"ts": now - i * 900, "price": price})
        return out
