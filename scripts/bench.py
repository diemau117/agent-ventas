"""Benchmark de latencia del Centro de Control contra un servidor vivo.

Uso:
    python scripts/bench.py <crm_token> [public_key] [n=50]

Mide p50/p95/max (ms) de los endpoints críticos multi-device. Sirve para
comparar antes/después de cualquier cambio y para la prueba de rendimiento
previa a la venta.

Notas:
- /api/chat depende del proveedor LLM: con GROQ_API_KEY mide red + modelo
  (segundos), sin key mide solo la capa de la app (milisegundos). Por eso se
  muestrean menos peticiones y se etiqueta el proveedor real.
"""
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Permite `python scripts/bench.py` desde cualquier sitio (repo en el path).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8099"


def _req(method: str, url: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        e.read()
        status = e.code
    return (time.perf_counter() - t0) * 1000.0, status


def _stats(samples: list[float]) -> dict:
    s = sorted(samples)
    return {
        "p50": statistics.median(s),
        "p95": s[max(0, min(len(s) - 1, int(0.95 * len(s)) - 1))],
        "max": s[-1],
        "avg": statistics.fmean(s),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    crm_token = sys.argv[1]
    public_key = sys.argv[2] if len(sys.argv) > 2 else ""
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 50

    from app.config import settings
    llm_label = "Groq+red" if settings.groq_api_key else "FakeProvider (solo app)"

    # Bearer del dispositivo (id único por ejecución)
    run = str(int(time.time() * 1000))
    req = urllib.request.Request(
        f"{BASE}/api/control-center/devices/register"
        f"?device_id=bench-{run}&name=Bench&token={crm_token}",
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        bearer = json.loads(r.read())["token"]

    # (label, method, path_template con {i}, bearer?, body?, muestras)
    cases = [
        ("GET  /health", "GET", "/health", False, None, n),
        ("POST /devices/register", "POST",
         f"/api/control-center/devices/register?device_id=bench-{run}-{{i}}&name=B&token={crm_token}",
         False, None, n),
        ("POST /ws-ticket", "POST", "/api/control-center/ws-ticket", True, None, n),
        ("GET  /state (resync)", "GET", "/api/control-center/state", True, None, n),
        ("GET  /usage", "GET", "/api/control-center/usage", True, None, n),
        ("GET  /devices", "GET", "/api/control-center/devices", True, None, n),
        ("POST /heartbeat", "POST", "/api/control-center/heartbeat", True, None, n),
        ("GET  /leads (legacy)", "GET", f"/api/control-center/leads?token={crm_token}", False, None, n),
        ("GET  /stats (legacy)", "GET", f"/api/control-center/stats?token={crm_token}", False, None, n),
    ]
    if public_key:
        # LLM: muestra corta (la latencia la domina el modelo, no la app)
        cases.append((f"POST /api/chat ({llm_label})", "POST", "/api/chat", False,
                      {"public_key": public_key, "message": "hola"}, 5))

    print(f"Benchmark contra {BASE} — LLM detectado: {llm_label}\n")
    print(f"{'endpoint':<38} {'n':>3} {'p50 ms':>8} {'p95 ms':>8} {'max ms':>8}  status")
    print("-" * 86)
    worst_p95 = 0.0
    worst_core_p95 = 0.0
    bad = []
    for label, method, path_t, use_token, body, samples in cases:
        samples_ms, statuses = [], set()
        for i in range(samples):
            path = path_t.replace("{i}", str(i))
            ms, status = _req(method, BASE + path, bearer if use_token else None, body)
            samples_ms.append(ms)
            statuses.add(status)
        st = _stats(samples_ms)
        worst_p95 = max(worst_p95, st["p95"])
        if not label.startswith("POST /api/chat"):
            worst_core_p95 = max(worst_core_p95, st["p95"])
        if statuses - {200}:
            bad.append((label, sorted(statuses)))
        print(f"{label:<38} {samples:>3} {st['p50']:>8.1f} {st['p95']:>8.1f} "
              f"{st['max']:>8.1f}  {sorted(statuses)}")

    print("-" * 86)
    print(f"Peor p95 de los endpoints de control: {worst_core_p95:.1f} ms")
    if any(c[0].startswith("POST /api/chat") for c in cases):
        print(f"Peor p95 con /api/chat (incluye el LLM): {worst_p95:.1f} ms")
    if bad:
        print(f"⚠️  Endpoints con status inesperado: {bad}")
        return 1
    print("OK — todos los endpoints respondieron 200")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
