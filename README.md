# 📈 Crypto Monitor

Monitor de precios de criptomonedas con alertas configurables, panel web en tiempo real y notificaciones por Telegram. Pensado para correr 24/7 en un VPS con Docker.

## Qué hace

- **Precios en tiempo real** de cualquier par de Binance (BTC, ETH, SOL... o el que añadas) usando su API pública — sin claves ni registros.
- **Alertas por reglas**: precio por encima/debajo de un umbral, o subidas/caídas porcentuales en la última hora o 24h.
- **Cooldown por regla** para que no te spamee (ej. no repetir la misma alerta en 30 min).
- **Panel web** con precios en vivo (SSE), sparklines, gráfico histórico, gestión de reglas y logs.
- **Telegram**: recibe cada alerta en tu móvil.
- **Modo demo** (`MOCK=1`): precios simulados para probar todo sin depender del mercado.
- **Contraseña opcional** para el panel, con bloqueo anti fuerza bruta (5 fallos → 15 min).

## Arranque local (para probar)

```bash
pip install -r requirements.txt

# Modo demo (precios simulados, no necesita internet)
MOCK=1 WEB_PASSWORD=prueba python run.py

# Modo real (API pública de Binance)
WEB_PASSWORD=prueba python run.py
```

Abre `http://127.0.0.1:8400` e introduce la contraseña.

## Despliegue en VPS (24/7)

Conéctate por SSH y ejecuta:

```bash
wget -qO install.sh https://raw.githubusercontent.com/algotrading1369-commits/Price-Alert/main/install.sh
sudo bash install.sh
```

El script instala Docker si falta, clona el repo, pregunta dominio (opcional) y contraseña (o genera una aleatoria), y arranca todo.

**Con dominio propio**: en el panel de tu dominio crea un registro **A** apuntando a la IP del VPS y abre los puertos **80 y 443**. Caddy (incluido) consigue y renueva el certificado de Let's Encrypt solo. Gratis también con [DuckDNS](https://www.duckdns.org): crea el subdominio (solo el prefijo, minúsculas y guiones), pon la IP del VPS en "current ip" y escribe el dominio completo (`monitor.duckdns.org`) en el instalador. El token de DuckDNS no hace falta para esto.

**Sin dominio**: el panel queda en `http://IP-DEL-VPS:8400`.

Comandos útiles en el servidor:

```bash
docker compose logs -f        # ver logs
docker compose restart        # reiniciar
docker compose down           # parar
cat /opt/crypto-monitor/.env  # ver la contraseña guardada
```

## Configurar Telegram

1. Habla con [@BotFather](https://t.me/BotFather) → `/newbot` → copia el **token**.
2. Habla con [@userinfobot](https://t.me/userinfobot) → copia tu **chat_id**.
3. En el panel, pestaña Ajustes → pega ambos, marca "Activar" y guarda. Llegará un mensaje de prueba.

## Alertas: tipos disponibles

| Tipo | Dispara cuando |
|---|---|
| Precio por encima de | el precio ≥ valor |
| Precio por debajo de | el precio ≤ valor |
| Sube un % en 1h | cambio de la última hora ≥ valor |
| Baja un % en 1h | cambio de la última hora ≤ −valor |
| Sube un % en 24h | cambio 24h ≥ valor |
| Baja un % en 24h | cambio 24h ≤ −valor |

El **cooldown** evita repetir la misma alerta hasta que pasen esos minutos.

## Variables de entorno

| Variable | Por defecto | Descripción |
|---|---|---|
| `WEB_PASSWORD` | (vacía = sin auth) | Contraseña del panel |
| `MOCK` | `0` | `1` = precios simulados |
| `POLL_SECONDS` | `15` | Intervalo entre consultas (mín. 5) |
| `DATA_DIR` | `./data` | Dónde guarda la base de datos |
| `BINANCE_API_URL` | `https://api.binance.com` | Por si necesitas otro endpoint (ej. `binance.us`) |

## Estructura

```
app/
  main.py        API FastAPI + auth + SSE
  engine.py      Motor: consulta precios y evalúa reglas
  db.py          SQLite (activos, reglas, histórico, logs)
  events.py      Bus de eventos para el panel en vivo
  notify.py      Telegram
  market/
    binance.py   Cliente API pública de Binance
    mock.py      Mercado simulado para el modo demo
  web/           Panel (HTML/CSS/JS vanilla, sin build)
```

## Notas

- Los datos son informativos; esto no es asesoramiento financiero ni ejecuta operaciones.
- Si Binance limita tu IP (muchas peticiones), sube `POLL_SECONDS`.
