"""Prueba de carga del Centro de Control (endpoint + WebSocket).

Uso:
    python scripts/load_test.py <crm_token> [concurrencia=20] [peticiones=200]

Mide:
1. Throughput y latencia de GET /control-center/state con N hilos concurrentes
2. Fan-out de WebSocket: N escuchas, un broadcast → tiempo hasta que todas reciben

Sirve para responder "¿cuántos clientes/chats aguanta?" con datos reales.
"""
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8099"
WS = "ws://127.0.0.1:8099"


def _get(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        e.read()
        status = e.code
    return (time.perf_counter() - t0) * 1000.0, status


def load_state(bearer, threads, total):
    results, errors = [], []
    lock = threading.Lock()
    per = max(1, total // threads)

    def worker():
        local = []
        for _ in range(per):
            ms, st = _get(f"{BASE}/api/control-center/state", bearer)
            local.append((ms, st))
        with lock:
            results.extend(local)

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    t0 = time.perf_counter()
    for t in ts: t.start()
    for t in ts: t.join()
    elapsed = time.perf_counter() - t0

    lat = sorted(m for m, s in results if s == 200)
    errors = [s for m, s in results if s != 200]
    print(f"  hilos={threads:>3}  peticiones={len(results):>5}  "
          f"RPS={len(results)/elapsed:>7.1f}  "
          f"p50={statistics.median(lat):>6.1f}ms  "
          f"p95={lat[int(0.95*len(lat))-1]:>7.1f}ms  "
          f"max={lat[-1]:>7.1f}ms  errores={len(errors)}")
    return len(results) / elapsed


def _ensure_conversation(crm_token: str) -> int:
    """Crea (o reutiliza) una conversación de ese tenant para generar eventos."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.config import settings
    from app.db.models import Business, Conversation

    url = settings.database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(url)
    with Session(engine) as s:
        biz = s.query(Business).filter(Business.crm_token == crm_token).first()
        if not biz:
            raise SystemExit("crm_token no encontrado en la BD")
        conv = Conversation(business_id=biz.id, channel="web", state="ai")
        s.add(conv)
        s.commit()
        return conv.id


def ws_fanout(bearer, conv_id, n_ws):
    """N WebSockets abiertos; un claim → tiempo hasta que todas reciben."""
    import asyncio
    import websockets

    async def run():
        from app.config import settings  # noqa: F401 (path)
        # Pedir tickets (uno por conexión)
        def _ask_tickets(n):
            out = []
            for _ in range(n):
                req = urllib.request.Request(f"{BASE}/api/control-center/ws-ticket", method="POST")
                req.add_header("Authorization", f"Bearer {bearer}")
                with urllib.request.urlopen(req, timeout=10) as r:
                    out.append(json.loads(r.read())["ticket"])
            return out

        tickets = await asyncio.to_thread(_ask_tickets, n_ws)

        async def listen(ticket):
            async with websockets.connect(f"{WS}/api/control-center/ws?ticket={ticket}") as ws:
                msg = await asyncio.wait_for(ws.recv(), timeout=30)
                return time.perf_counter()

        conns = [asyncio.create_task(listen(t)) for t in tickets]
        await asyncio.sleep(0.5)  # dejar que todas conecten

        # Disparar un evento REAL: claim que genera conversation_claimed.
        # ¡OJO! Debe correr en un hilo: si bloquea el event loop, los sockets
        # no pueden leer y el propio test se autocongela.
        def _claim():
            r = urllib.request.Request(
                f"{BASE}/api/control-center/conversations/{conv_id}/claim", method="POST")
            r.add_header("Authorization", f"Bearer {bearer}")
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())

        t_sent = time.perf_counter()  # instante en que se emite el evento
        try:
            status, body = await asyncio.to_thread(_claim)
            claim_ms = (time.perf_counter() - t_sent) * 1000
        except Exception as e:
            status, body, claim_ms = f"ERROR {e}", {}, (time.perf_counter() - t_sent) * 1000

        recibidas = 0
        for c in conns:
            try:
                await c
                recibidas += 1
            except Exception:
                pass
        dt = (time.perf_counter() - t_sent) * 1000  # evento → última en llegar
        return recibidas, dt, status, claim_ms

    recibidas, dt, status, claim_ms = asyncio.run(run())
    print(f"  websockets={n_ws:>4}  recibieron={recibidas:>4}  "
          f"claim={status} ({claim_ms:.1f} ms)  "
          f"fan-out (evento → últimamente en llegar)={dt:>8.1f} ms")
    return recibidas


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    crm = sys.argv[1]
    threads = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    total = int(sys.argv[3]) if len(sys.argv) > 3 else 200

    req = urllib.request.Request(
        f"{BASE}/api/control-center/devices/register"
        f"?device_id=load-{int(time.time()*1000)}&name=Load&token={crm}", method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        bearer = json.loads(r.read())["token"]

    print("\n1) Carga HTTP — GET /control-center/state")
    for th in (1, threads, threads * 2):
        load_state(bearer, th, total)

    conv_id = _ensure_conversation(crm)
    print(f"\n2) WebSocket — fan-out por número de paneles abiertos (conv #{conv_id})")
    for n in (5, 20, 50):
        ws_fanout(bearer, conv_id, n)

    print("\nNota: números locales. En Render Free la CPU es compartida y hay")
    print("1 sola instancia; multiplica/divide en función de ese entorno.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
