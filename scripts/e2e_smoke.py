"""Smoke E2E multi-device contra un servidor uvicorn vivo.

Uso:
    python scripts/e2e_smoke.py <conversation_id> <crm_token>

Cubre el flujo completo del Centro de Control multi-device:
1. Registro de 2 dispositivos (obtienen Bearer token)
2. Ticket de WebSocket de un solo uso (el Bearer NO viaja en la URL)
3. WebSocket abierto en PC2
4. PC1 toma la conversación → PC2 recibe conversation_claimed al instante
5. PC2 intenta tomar la misma → 409 (claim atómico)
6. GET /state con Bearer → resync OK
7. PC1 libera → PC2 recibe conversation_released
8. Sin Bearer → 401 · Consumo IA con niveles de alerta
"""
import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

import websockets

BASE = "http://127.0.0.1:8099"
WS = "ws://127.0.0.1:8099"


def _request(method: str, url: str, token: str | None = None):
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body}


async def main() -> int:
    if len(sys.argv) < 3:
        print("Uso: e2e_smoke.py <conversation_id> <crm_token>")
        return 2

    conv_id, crm_token = sys.argv[1], sys.argv[2]
    run = int(time.time() * 1000)

    # 1. Registro de dos dispositivos del mismo negocio
    s1, d1 = _request(
        "POST",
        f"{BASE}/api/control-center/devices/register"
        f"?device_id=pc1-{run}&name=PC%20Uno&token={crm_token}",
    )
    s2, d2 = _request(
        "POST",
        f"{BASE}/api/control-center/devices/register"
        f"?device_id=pc2-{run}&name=PC%20Dos&token={crm_token}",
    )
    assert s1 == 200 and s2 == 200, f"registro falló: {s1} {s2}"
    tok1, tok2 = d1["token"], d2["token"]
    print(f"[1] OK  registro 2 dispositivos ({d1['device_id']}, {d2['device_id']})")

    # 2. Ticket de WebSocket one-time (Bearer en header, jamás en la URL)
    s, tick = _request("POST", f"{BASE}/api/control-center/ws-ticket", tok2)
    assert s == 200 and tick.get("single_use"), f"ws-ticket → {s}: {tick}"
    ticket = tick["ticket"]
    print(f"[2] OK  ticket WS emitido (one-time, {tick['expires_in']}s)")

    # 2b. El token permanente en la URL debe ser rechazado (endurecimiento)
    try:
        async with websockets.connect(f"{WS}/api/control-center/ws?token={tok2}") as bad:
            await bad.recv()
        raise AssertionError("el WebSocket aceptó un token permanente en la URL")
    except Exception as e:
        assert "aceptó" not in str(e), e
        print("[2b] OK WebSocket rechaza token permanente en la URL")

    # 3. WebSocket en PC2 con el ticket
    async with websockets.connect(f"{WS}/api/control-center/ws?ticket={ticket}") as ws:
        await ws.send("ping")
        pong = await asyncio.wait_for(ws.recv(), timeout=5)
        assert pong == "pong", pong
        print("[3] OK  WebSocket conectado (ping/pong)")

        # 2c. El ticket ya se consumió: no sirve para una segunda conexión
        try:
            async with websockets.connect(f"{WS}/api/control-center/ws?ticket={ticket}") as again:
                await again.recv()
            raise AssertionError("el ticket se reutilizó")
        except Exception as e:
            assert "reutilizó" not in str(e), e
            print("[2c] OK  ticket de un solo uso (segunda conexión rechazada)")

        s, body = _request("POST", f"{BASE}/api/control-center/conversations/{conv_id}/claim", tok1)
        assert s == 200, f"claim PC1 → {s}: {body}"
        print(f"[4] OK  PC1 tomó la conversación: lease hasta {body['lease_expires_at']}")

        evt = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert evt["type"] == "conversation_claimed", evt
        assert evt["data"]["conversation_id"] == int(conv_id), evt
        assert evt["data"]["assigned_to"] == "PC Uno", evt
        print(f"[5] OK  PC2 recibió evento en vivo: {evt['type']} → {evt['data']['assigned_to']}")

        s, body = _request("POST", f"{BASE}/api/control-center/conversations/{conv_id}/claim", tok2)
        assert s == 409, f"segundo claim debería ser 409, fue {s}"
        print(f"[6] OK  PC2 recibió 409 (claim atómico): {body.get('detail')}")

        # Resync de estado
        s, state = _request("GET", f"{BASE}/api/control-center/state", tok1)
        assert s == 200 and "conversations" in state, f"state → {s}"
        me = [c for c in state["conversations"] if c["id"] == int(conv_id)]
        assert me and me[0]["assigned_to"] == "PC Uno", me
        print(f"[7] OK  GET /state (resync): {len(state['conversations'])} conversaciones, "
              f"{len(state['leads'])} leads")

        # Liberar → evento en vivo
        s, body = _request("POST", f"{BASE}/api/control-center/conversations/{conv_id}/release", tok1)
        assert s == 200, f"release → {s}"
        evt = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert evt["type"] == "conversation_released", evt
        print(f"[8] OK  PC2 recibió {evt['type']} en vivo")

    # 9. Auth: sin token no hay acceso
    s, _ = _request("GET", f"{BASE}/api/control-center/state")
    assert s == 401, f"state sin token debería ser 401, fue {s}"
    print("[9] OK  estado sin Bearer → 401")

    # 10. Consumo IA con nivel de alerta
    s, usage = _request("GET", f"{BASE}/api/control-center/usage", tok1)
    assert s == 200 and usage.get("level") in {"ok", "attention", "warning", "critical", "blocked"}, usage
    print(f"[10] OK consumo IA: {usage['percent']}% ({usage['level']}) "
          f"{usage['tokens_today']}/{usage['daily_token_budget']} tokens")

    print("\nSMOKE E2E MULTI-DEVICE: 10/10 OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
