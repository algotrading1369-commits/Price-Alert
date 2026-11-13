import asyncio
import json
import os
import queue
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from .db import Database
from .engine import Engine, RULE_KINDS, describe_rule
from .events import EventBus
from .notify import TelegramNotifier

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PANEL_PASSWORD = os.environ.get("WEB_PASSWORD", "")

_auth_lock = threading.Lock()
_auth_fails = {}
MAX_AUTH_FAILS = 5
AUTH_BLOCK_SECONDS = 900


def _client_ip(request: Request):
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _auth_blocked(ip):
    with _auth_lock:
        fails = _auth_fails.get(ip)
        if not fails:
            return False
        count, last = fails
        if time.time() - last > AUTH_BLOCK_SECONDS:
            del _auth_fails[ip]
            return False
        return count >= MAX_AUTH_FAILS


def _register_auth_fail(ip):
    with _auth_lock:
        count, last = _auth_fails.get(ip, (0, 0))
        if time.time() - last > AUTH_BLOCK_SECONDS:
            count = 0
        _auth_fails[ip] = (count + 1, time.time())


def _auth_ok(request: Request):
    sent = request.headers.get("X-Panel-Password") or request.query_params.get("key")
    if sent == PANEL_PASSWORD:
        with _auth_lock:
            _auth_fails.pop(_client_ip(request), None)
        return True
    _register_auth_fail(_client_ip(request))
    return False


app = FastAPI(title="Crypto Price Monitor")
db = Database()
bus = EventBus()
notifier = TelegramNotifier(db)
engine = Engine(db, bus, notifier)
engine.start()


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if PANEL_PASSWORD:
        path = request.url.path
        if (path.startswith("/api") or path == "/") and _auth_blocked(_client_ip(request)):
            return JSONResponse(status_code=429, content={"detail": "Demasiados intentos fallidos. Espera 15 minutos."})
        needs_auth = path.startswith("/api") or path == "/"
        if needs_auth and not _auth_ok(request):
            if path.startswith("/api"):
                return JSONResponse(status_code=401, content={"detail": "Contraseña incorrecta"})
    return await call_next(request)


class AssetIn(BaseModel):
    symbol: str
    name: str = ""


class RuleIn(BaseModel):
    symbol: str
    kind: str
    value: float
    cooldown_min: int = 30


class RuleUpdate(BaseModel):
    value: float | None = None
    cooldown_min: int | None = None
    enabled: bool | None = None


class ConfigIn(BaseModel):
    poll_seconds: int | None = None


class NotifyIn(BaseModel):
    token: str = ""
    chat_id: str = ""
    enabled: bool = False


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


@app.get("/static/{path}")
def static_files(path: str):
    full = os.path.join(WEB_DIR, "static", path)
    if not os.path.isfile(full):
        raise HTTPException(404)
    return FileResponse(full)


@app.get("/api/status")
def status():
    data = engine.snapshot()
    assets = []
    for asset in db.assets():
        latest = db.latest_price(asset["symbol"])
        assets.append({
            "symbol": asset["symbol"],
            "name": asset["name"],
            "price": latest["price"] if latest else None,
            "change_24h": latest["change_24h"] if latest else None,
            "updated": latest["ts"] if latest else None,
        })
    data["assets"] = assets
    data["rules_count"] = len(db.rules())
    data["auth_required"] = bool(PANEL_PASSWORD)
    return data


@app.get("/api/kinds")
def kinds():
    return {"kinds": [{"kind": k, "label": v} for k, v in RULE_KINDS.items()]}


@app.get("/api/assets")
def list_assets():
    return {"assets": db.assets()}


@app.post("/api/assets")
def add_asset(payload: AssetIn):
    symbol = payload.symbol.strip().upper()
    if not symbol.isalnum():
        raise HTTPException(400, "El símbolo solo puede contener letras y números")
    try:
        info = engine.market.ticker_batch([symbol])
    except Exception as exc:
        raise HTTPException(400, f"Símbolo no válido: {exc}")
    if not db.add_asset(symbol, payload.name.strip()):
        raise HTTPException(400, "Ese símbolo ya está en la lista")
    try:
        for point in engine.market.klines(symbol, interval="15m", limit=96):
            db.add_price(symbol, point["ts"], point["price"], 0)
    except Exception:
        pass
    db.add_log("info", f"Activo añadido: {symbol}")
    bus.publish("log", level="info", message=f"Activo añadido: {symbol}")
    return {"ok": True, "price": info[symbol]["price"]}


@app.delete("/api/assets/{symbol}")
def delete_asset(symbol: str):
    if not db.remove_asset(symbol):
        raise HTTPException(404, "No existe ese activo")
    db.add_log("info", f"Activo eliminado: {symbol.upper()}")
    return {"ok": True}


@app.get("/api/rules")
def list_rules():
    rules = db.rules()
    for rule in rules:
        rule["description"] = describe_rule(rule["kind"], rule["value"])
    return {"rules": rules}


@app.post("/api/rules")
def create_rule(payload: RuleIn):
    if payload.kind not in RULE_KINDS:
        raise HTTPException(400, "Tipo de alerta no válido")
    if payload.value <= 0:
        raise HTTPException(400, "El valor debe ser mayor que cero")
    asset = None
    for a in db.assets():
        if a["symbol"] == payload.symbol.upper():
            asset = a
            break
    if asset is None:
        raise HTTPException(400, "Ese símbolo no está en tu lista de activos")
    rule_id = db.add_rule(asset["id"], payload.kind, payload.value, max(1, payload.cooldown_min))
    db.add_log("info", f"Alerta creada: {payload.symbol.upper()} {describe_rule(payload.kind, payload.value)}")
    bus.publish("log", level="info", message=f"Alerta creada: {payload.symbol.upper()} {describe_rule(payload.kind, payload.value)}")
    return {"ok": True, "id": rule_id}


@app.put("/api/rules/{rule_id}")
def patch_rule(rule_id: int, payload: RuleUpdate):
    db.update_rule(rule_id, payload.value, payload.cooldown_min, payload.enabled)
    return {"ok": True}


@app.delete("/api/rules/{rule_id}")
def remove_rule(rule_id: int):
    db.delete_rule(rule_id)
    return {"ok": True}


@app.get("/api/alerts")
def alerts(limit: int = 50):
    return {"alerts": db.recent_alerts(min(limit, 200))}


@app.get("/api/history")
def history(symbol: str, hours: float = 24, points: int = 400):
    since = time.time() - hours * 3600
    rows = db.history(symbol.upper(), since)
    if len(rows) > points:
        stride = len(rows) / points
        rows = [rows[int(i * stride)] for i in range(points)]
    return {"symbol": symbol.upper(), "points": rows}


@app.get("/api/config")
def get_config():
    return {"poll_seconds": engine.poll_interval(), "mock": engine.mock}


@app.put("/api/config")
def put_config(payload: ConfigIn):
    if payload.poll_seconds is not None:
        db.set_setting("poll_seconds", max(5, int(payload.poll_seconds)))
    return {"ok": True}


@app.get("/api/notify")
def get_notify():
    cfg = notifier.config()
    return {"token": cfg.get("token", ""), "chat_id": cfg.get("chat_id", ""), "enabled": cfg.get("enabled", False)}


@app.post("/api/notify")
def set_notify(payload: NotifyIn):
    notifier.save_config(payload.token, payload.chat_id, payload.enabled)
    if payload.enabled:
        ok = notifier.send("✅ <b>Crypto Monitor</b> conectado. Te avisaré cuando salte una alerta.")
        if not ok:
            raise HTTPException(400, "No se pudo enviar el mensaje de prueba: revisa token y chat_id")
    return {"ok": True}


@app.get("/api/logs")
def logs(since: float = 0, limit: int = 300):
    return db.recent_logs(since, min(limit, 1000))


@app.get("/api/events")
def events(since: float = 0):
    return {"events": bus.history(since)}


@app.get("/api/stream")
async def stream(request: Request):
    if PANEL_PASSWORD and request.headers.get("X-Panel-Password") != PANEL_PASSWORD and request.query_params.get("key") != PANEL_PASSWORD:
        raise HTTPException(401, "Contraseña incorrecta")
    q = bus.subscribe()

    async def generator():
        try:
            yield f"data: {json.dumps({'kind': 'hello', 'ts': 0})}\n\n"
            while True:
                try:
                    item = await asyncio.to_thread(q.get, True, 30)
                    yield f"data: {item}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(generator(), media_type="text/event-stream")
