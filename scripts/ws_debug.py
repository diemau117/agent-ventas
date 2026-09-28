"""Debug de concurrencía WebSocket: ¿cuántos paneles abiertos aguanta el servidor?

Uso:
    python scripts/ws_debug.py <crm_token> [n=5]

Abre N WebSockets concurrentes, comprueba que el servidor sigue respondiendo
(heartbeat) y que cada conexión recibe los eventos emitidos.
"""
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8099"
WS = "ws://127.0.0.1:8099"


def http(method, url, token=None):
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    crm = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    run = int(time.time() * 1000)

    dev = await asyncio.to_thread(
        http, "POST",
        f"{BASE}/api/control-center/devices/register?device_id=dbg-{run}&name=Dbg&token={crm}")
    tok = dev["token"]

    def ask(count):
        return [http("POST", f"{BASE}/api/control-center/ws-ticket", tok)["ticket"]
                for _ in range(count)]
    tickets = await asyncio.to_thread(ask, n)

    results = []

    async def listen(i, ticket):
        t0 = time.perf_counter()
        try:
            async with websockets.connect(
                f"{WS}/api/control-center/ws?ticket={ticket}", open_timeout=8
            ) as ws:
                tconn = (time.perf_counter() - t0) * 1000
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=8)
                    results.append((i, "OK", f"conectó {tconn:.0f} ms · {msg[:30]}"))
                except asyncio.TimeoutError:
                    results.append((i, "SIN_EVENTO", f"conectó {tconn:.0f} ms"))
        except Exception as e:
            results.append((i, "FALLO", f"{type(e).__name__}: {e}"))

    tasks = [asyncio.create_task(listen(i, t)) for i, t in enumerate(tickets)]
    await asyncio.sleep(1.0)

    t0 = time.perf_counter()
    try:
        await asyncio.to_thread(http, "POST", f"{BASE}/api/control-center/heartbeat", tok)
        print(f"  servidor vivo: heartbeat {((time.perf_counter() - t0) * 1000):.0f} ms")
    except Exception as e:
        print(f"  servidor BLOQUEADO: heartbeat falló ({e})")

    await asyncio.gather(*tasks, return_exceptions=True)
    for r in sorted(results)[:10]:
        print("   ", r)
    if len(results) > 10:
        print(f"    … ({len(results)} en total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
