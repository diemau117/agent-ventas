/* widget.js — widget de chat de Agent Ventas, autocontenido (sin dependencias).
 *
 * Configuración de la clave pública del negocio (NUNCA se muestra ni se pide
 * business_id al usuario):
 *   <script src="widget.js" data-public-key="..."></script>
 *   o bien: window.AGENT_VENTAS_KEY = "..."
 *
 * Montaje: dentro de <div id="agent-ventas"></div>; si no existe el
 * contenedor, crea una burbuja flotante. Todo el contenido proveniente del
 * LLM se pinta con textContent (sin innerHTML → sin XSS).
 */
(function () {
  "use strict";

  const CHAT_ENDPOINT = "/api/chat";
  const LS_KEY = "av_key";
  const LS_CONV = "av_conv_";

  const MSG = {
    sinClave:
      "La conversación no está disponible ahora. Dejanos tus datos en el formulario de contacto y te respondemos a la brevedad.",
    rate: "Estamos recibiendo muchos mensajes. Probá de nuevo en un minuto.",
    enviar: "No pudimos enviar tu mensaje. Probá de nuevo en unos segundos.",
    abrir:
      "No pudimos iniciar la conversación. Probá de nuevo en unos segundos.",
    sinRed: "Sin conexión. Revisá tu internet y volvé a intentar.",
    escribiendo: "Escribiendo…",
  };

  const script = document.currentScript;

  function clean(v) {
    return typeof v === "string" ? v.trim() : "";
  }

  function leer(k) {
    try {
      return localStorage.getItem(k) || "";
    } catch (e) {
      return "";
    }
  }

  function guardar(k, v) {
    try {
      localStorage.setItem(k, v);
    } catch (e) {
      /* sin storage la conversación sigue funcionando en esta sesión */
    }
  }

  function borrar(k) {
    try {
      localStorage.removeItem(k);
    } catch (e) {
      /* idem */
    }
  }

  /* Prioridad: data-public-key del <script> → ventana → ?key= → recordada. */
  function resolverClave() {
    const attr = script && script.dataset ? clean(script.dataset.publicKey) : "";
    if (attr) return attr;
    const global = clean(window.AGENT_VENTAS_KEY);
    if (global) return global;
    let url = "";
    try {
      url = clean(new URLSearchParams(location.search).get("key"));
    } catch (e) {
      url = "";
    }
    if (url) {
      guardar(LS_KEY, url);
      return url;
    }
    return clean(leer(LS_KEY));
  }

  const publicKey = resolverClave();
  let convId = null;
  if (publicKey) {
    const guardada = parseInt(leer(LS_CONV + publicKey), 10);
    convId = Number.isInteger(guardada) && guardada > 0 ? guardada : null;
  }
  let iniciada = false;
  let ocupado = false;

  function nodo(tag, cls, texto) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (texto !== undefined && texto !== null) n.textContent = texto;
    return n;
  }

  /* --- Estilos inyectados (el widget es autocontenido) ------------------ */

  const CSS = `/* Hallmark · component: chat-widget · genre: editorial
 * states: default · hover · focus-visible · active · disabled · loading · error · success
 * contrast: pass */
.av-root {
  --av-paper: oklch(98.5% 0.004 95);
  --av-surface: oklch(100% 0 0);
  --av-ink: oklch(23% 0.02 260);
  --av-muted: oklch(45% 0.015 265);
  --av-line: oklch(89% 0.008 260);
  --av-accent: oklch(45% 0.14 255);
  --av-accent-strong: oklch(38% 0.13 255);
  --av-on-accent: oklch(99% 0 0);
  --av-error: oklch(50% 0.16 27);
  --av-radius-sm: 6px;
  --av-radius-md: 10px;
  --av-radius-lg: 16px;
  --av-shadow: 0 2px 6px oklch(23% 0.02 260 / 0.12),
    0 16px 40px oklch(23% 0.02 260 / 0.18);
  --av-ease: cubic-bezier(0.2, 0.8, 0.3, 1);
  --av-dur: 120ms;
  --av-font-body: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --av-font-display: Georgia, "Iowan Old Style", "Times New Roman", serif;
  font-family: var(--av-font-body);
  font-size: 15px;
  line-height: 1.5;
  color: var(--av-ink);
}
.av-root *, .av-root *::before, .av-root *::after { box-sizing: border-box; }
.av-root :focus-visible {
  outline: 2px solid var(--av-accent);
  outline-offset: 2px;
}
.av-root--inline {
  display: block;
  height: clamp(26rem, 70vh, 34rem);
}
.av-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--av-surface);
  border: 1px solid var(--av-line);
  border-radius: var(--av-radius-lg);
  overflow: hidden;
}
.av-root--inline .av-panel { height: 100%; border: none; border-radius: 0; }
.av-root--float .av-panel {
  position: fixed;
  right: 1rem;
  bottom: 5.5rem;
  width: min(23rem, calc(100vw - 2rem));
  height: min(32rem, calc(100vh - 7rem));
  height: min(32rem, calc(100dvh - 7rem));
  z-index: 9998;
  box-shadow: var(--av-shadow);
}
.av-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--av-gap, 0.5rem);
  padding: 0.7rem 1rem;
  background: var(--av-paper);
  border-bottom: 1px solid var(--av-line);
}
.av-head-texto { min-width: 0; }
.av-head-titulo {
  font-family: var(--av-font-display);
  font-size: 1rem;
  font-weight: 700;
  margin: 0;
  overflow-wrap: anywhere;
}
.av-head-sub { font-size: 0.75rem; color: var(--av-muted); margin: 0; }
.av-cerrar {
  flex: 0 0 auto;
  min-width: 2.25rem;
  min-height: 2.25rem;
  padding: 0.25rem 0.5rem;
  background: transparent;
  border: 1px solid var(--av-line);
  border-radius: var(--av-radius-sm);
  font: inherit;
  font-size: 0.8125rem;
  color: var(--av-ink);
  cursor: pointer;
  transition: border-color var(--av-dur) var(--av-ease);
}
.av-cerrar:hover { border-color: var(--av-accent); }
.av-log {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 0.9rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  background: var(--av-surface);
}
.av-msg {
  max-width: 85%;
  padding: 0.5rem 0.75rem;
  border-radius: var(--av-radius-md);
  font-size: 0.9375rem;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.av-msg--user {
  align-self: flex-end;
  background: var(--av-accent);
  color: var(--av-on-accent);
  border-bottom-right-radius: 3px;
}
.av-msg--bot {
  align-self: flex-start;
  background: var(--av-paper);
  border: 1px solid var(--av-line);
  border-bottom-left-radius: 3px;
}
.av-msg--estado {
  align-self: center;
  max-width: 100%;
  padding: 0.25rem 0.5rem;
  background: none;
  text-align: center;
  font-size: 0.8125rem;
  color: var(--av-muted);
}
.av-msg--error { color: var(--av-error); font-weight: 600; }
.av-typing {
  align-self: flex-start;
  padding-inline: 0.75rem;
  font-size: 0.8125rem;
  color: var(--av-muted);
}
.av-cards {
  align-self: flex-start;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  max-width: 92%;
}
.av-card {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: 0.65rem 0.75rem;
  background: var(--av-surface);
  border: 1px solid var(--av-line);
  border-radius: var(--av-radius-md);
}
.av-card-nombre { margin: 0; font-size: 0.9rem; font-weight: 700; }
.av-card-desc { margin: 0; font-size: 0.8125rem; color: var(--av-muted); }
.av-card-precio { margin: 0; font-size: 0.875rem; font-weight: 700; color: var(--av-accent-strong); }
.av-card-cta {
  margin-top: 0.4rem;
  align-self: flex-start;
  min-height: 36px;
  padding: 0.4rem 0.8rem;
  background: transparent;
  border: 1px solid var(--av-accent);
  border-radius: var(--av-radius-sm);
  font: inherit;
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--av-accent-strong);
  white-space: nowrap;
  cursor: pointer;
  transition: background-color var(--av-dur) var(--av-ease);
}
.av-card-cta:hover:not(:disabled) { background: oklch(95% 0.025 255); }
.av-card-cta:active:not(:disabled) { transform: translateY(1px); }
.av-card-cta:disabled { opacity: 0.6; cursor: not-allowed; }
.av-form {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem;
  background: var(--av-paper);
  border-top: 1px solid var(--av-line);
}
.av-input {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 44px;
  padding: 0.5rem 0.75rem;
  font: inherit;
  font-size: 0.9375rem;
  color: var(--av-ink);
  background: var(--av-surface);
  border: 1px solid var(--av-line);
  border-radius: var(--av-radius-sm);
  transition: border-color var(--av-dur) var(--av-ease);
}
.av-input:hover { border-color: var(--av-muted); }
.av-input:focus-visible { border-color: var(--av-accent); }
.av-input:disabled { opacity: 0.65; }
.av-send {
  flex: 0 0 auto;
  min-height: 44px;
  padding: 0.5rem 1rem;
  background: var(--av-accent);
  border: none;
  border-radius: var(--av-radius-sm);
  font: inherit;
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--av-on-accent);
  white-space: nowrap;
  cursor: pointer;
  transition: background-color var(--av-dur) var(--av-ease);
}
.av-send:hover:not(:disabled) { background: var(--av-accent-strong); }
.av-send:active:not(:disabled) { transform: translateY(1px); }
.av-send:disabled { opacity: 0.6; cursor: not-allowed; }
.av-fab {
  position: fixed;
  right: 1rem;
  bottom: 1rem;
  z-index: 9999;
  min-height: 3.25rem;
  padding: 0.65rem 1.1rem;
  background: var(--av-accent);
  border: none;
  border-radius: var(--av-radius-lg);
  font: inherit;
  font-size: 0.9375rem;
  font-weight: 700;
  color: var(--av-on-accent);
  white-space: nowrap;
  cursor: pointer;
  box-shadow: var(--av-shadow);
  transition: background-color var(--av-dur) var(--av-ease);
}
.av-fab:hover { background: var(--av-accent-strong); }
.av-fab:active { transform: translateY(1px); }
@media (prefers-reduced-motion: reduce) {
  .av-root *, .av-root *::before, .av-root *::after {
    transition-duration: 100ms !important;
    animation: none !important;
  }
  .av-send:active, .av-fab:active, .av-card-cta:active { transform: none; }
}`;

  function inyectarEstilos() {
    if (document.getElementById("av-widget-styles")) return;
    const s = document.createElement("style");
    s.id = "av-widget-styles";
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  /* --- Construcción de la interfaz --------------------------------------- */

  let log;
  let input;
  let form;
  let enviar;
  let typing;

  function construirPanel(raiz, flotante) {
    const panel = nodo("div", "av-panel");
    panel.id = "av-panel";
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-label", "Chat con el agente de ventas");

    const head = nodo("div", "av-head");
    const texto = nodo("div", "av-head-texto");
    texto.appendChild(nodo("p", "av-head-titulo", "Agente de ventas"));
    texto.appendChild(nodo("p", "av-head-sub", "Respuesta inmediata"));
    head.appendChild(texto);
    if (flotante) {
      const cerrar = nodo("button", "av-cerrar", "Cerrar");
      cerrar.type = "button";
      cerrar.setAttribute("aria-label", "Cerrar el chat");
      cerrar.addEventListener("click", () => {
        panel.hidden = true;
        const fab = document.getElementById("av-fab");
        if (fab) {
          fab.setAttribute("aria-expanded", "false");
          fab.focus();
        }
      });
      head.appendChild(cerrar);
    }
    panel.appendChild(head);

    log = nodo("div", "av-log");
    log.setAttribute("role", "log");
    log.setAttribute("aria-live", "polite");
    log.setAttribute("aria-relevant", "additions");
    log.setAttribute("aria-label", "Mensajes de la conversación");
    log.tabIndex = 0;
    panel.appendChild(log);

    form = nodo("form", "av-form");
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      enviarMensaje(input.value);
    });
    input = document.createElement("input");
    input.className = "av-input";
    input.type = "text";
    input.placeholder = "Escribí tu consulta…";
    input.maxLength = 2000;
    input.setAttribute("aria-label", "Mensaje para el agente");
    input.autocomplete = "off";
    form.appendChild(input);
    enviar = nodo("button", "av-send", "Enviar");
    enviar.type = "submit";
    form.appendChild(enviar);
    panel.appendChild(form);

    typing = nodo("div", "av-typing", MSG.escribiendo);
    typing.hidden = true;

    raiz.appendChild(panel);
  }

  function montar() {
    inyectarEstilos();
    if (document.getElementById("av-root")) return;

    const anfitrion = document.getElementById("agent-ventas");
    const raiz = nodo("div", "av-root");
    raiz.id = "av-root";

    if (anfitrion) {
      raiz.classList.add("av-root--inline");
      anfitrion.appendChild(raiz);
      construirPanel(raiz, false);
      // En la landing la conversación arranca al cargar la sección.
      iniciarConversacion();
    } else {
      raiz.classList.add("av-root--float");
      document.body.appendChild(raiz);
      construirPanel(raiz, true);
      const panel = document.getElementById("av-panel");
      panel.hidden = true;

      const fab = nodo("button", "av-fab", "¿Hablamos?");
      fab.id = "av-fab";
      fab.type = "button";
      fab.setAttribute("aria-expanded", "false");
      fab.setAttribute("aria-controls", "av-panel");
      fab.addEventListener("click", () => {
        const abierto = !panel.hidden;
        panel.hidden = abierto;
        fab.setAttribute("aria-expanded", String(!abierto));
        if (!abierto) {
          input.focus();
          if (!iniciada) iniciarConversacion();
        }
      });
      raiz.appendChild(fab);
    }

    if (!publicKey) {
      mostrarEstado(MSG.sinClave, true);
      ocupado = true;
      input.disabled = true;
      enviar.disabled = true;
    }
  }

  /* --- Conversación ------------------------------------------------------ */

  function post(body) {
    return fetch(CHAT_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function mensajeError(status, porDefecto) {
    if (status === 401) return MSG.sinClave;
    if (status === 429) return MSG.rate;
    return porDefecto;
  }

  function guardarConversacion(id) {
    const n = Number(id);
    if (Number.isInteger(n) && n > 0 && publicKey) {
      convId = n;
      guardar(LS_CONV + publicKey, String(n));
    }
  }

  function borrarConversacion() {
    if (publicKey) borrar(LS_CONV + publicKey);
    convId = null;
  }

  function iniciarConversacion() {
    if (iniciada) return;
    iniciada = true;
    if (!publicKey) return;
    mostrarTyping(true);
    post({ public_key: publicKey, conversation_id: convId, greeting: true })
      .then(async (r) => {
        if (r.status === 404 && convId !== null) {
          // Conversación vieja (por ejemplo, base reiniciada): se empieza de nuevo.
          borrarConversacion();
          r = await post({ public_key: publicKey, conversation_id: null, greeting: true });
        }
        if (!r.ok) {
          mostrarTyping(false);
          mostrarEstado(mensajeError(r.status, MSG.abrir), true);
          return;
        }
        const data = await r.json();
        mostrarTyping(false);
        guardarConversacion(data.conversation_id);
        pintarRespuesta(data);
      })
      .catch(() => {
        mostrarTyping(false);
        mostrarEstado(MSG.sinRed, true);
      });
  }

  async function enviarMensaje(cruudo) {
    const texto = clean(cruudo);
    if (!texto || ocupado) return;
    if (!publicKey) {
      mostrarEstado(MSG.sinClave, true);
      return;
    }
    ocupado = true;
    ocuparFormulario(true);
    agregar(texto, "user");
    input.value = "";
    mostrarTyping(true);
    try {
      let r = await post({ public_key: publicKey, conversation_id: convId, message: texto });
      if (r.status === 404 && convId !== null) {
        borrarConversacion();
        r = await post({ public_key: publicKey, conversation_id: null, message: texto });
      }
      if (!r.ok) {
        mostrarTyping(false);
        mostrarEstado(mensajeError(r.status, MSG.enviar), true);
        return;
      }
      const data = await r.json();
      mostrarTyping(false);
      guardarConversacion(data.conversation_id);
      pintarRespuesta(data);
    } catch (e) {
      mostrarTyping(false);
      mostrarEstado(MSG.sinRed, true);
    } finally {
      ocupado = false;
      ocuparFormulario(false);
    }
  }

  /* --- Render (solo textContent: sin innerHTML) --------------------------- */

  function bajarAlFinal() {
    log.scrollTop = log.scrollHeight;
  }

  function agregar(texto, clase) {
    const n = nodo("div", "av-msg av-msg--" + clase, texto);
    log.appendChild(n);
    bajarAlFinal();
    return n;
  }

  function mostrarEstado(texto, error) {
    const n = nodo(
      "div",
      "av-msg av-msg--estado" + (error ? " av-msg--error" : ""),
      texto
    );
    log.appendChild(n);
    bajarAlFinal();
  }

  function mostrarTyping(ver) {
    if (!typing) return;
    if (ver) {
      log.appendChild(typing);
      bajarAlFinal();
    } else if (typing.parentNode) {
      typing.parentNode.removeChild(typing);
    }
  }

  function ocuparFormulario(ocupada) {
    input.disabled = ocupada;
    enviar.disabled = ocupada;
    log.querySelectorAll(".av-card-cta").forEach((b) => {
      b.disabled = ocupada;
    });
  }

  function pintarRespuesta(data) {
    let partes = Array.isArray(data.replies) ? data.replies : [];
    if (!partes.length && typeof data.reply === "string" && data.reply) {
      partes = [data.reply];
    }
    partes.forEach((p) => {
      const t = clean(p);
      if (t) agregar(t, "bot");
    });

    const tarjetas = Array.isArray(data.cards) ? data.cards : [];
    if (tarjetas.length) pintarTarjetas(tarjetas);
  }

  function pintarTarjetas(tarjetas) {
    const wrap = nodo("div", "av-cards");
    tarjetas.slice(0, 4).forEach((c) => {
      if (!c || typeof c !== "object") return;
      const card = nodo("div", "av-card");
      const nombre = clean(c.name);
      const desc = clean(c.description);
      const precio = clean(c.price_label);
      const cta = clean(c.cta);
      if (nombre) card.appendChild(nodo("p", "av-card-nombre", nombre));
      if (desc) card.appendChild(nodo("p", "av-card-desc", desc));
      if (precio) card.appendChild(nodo("p", "av-card-precio", precio));
      if (cta) {
        // El CTA viaja como mensaje normal: pasa por filtro y verificador.
        const b = nodo("button", "av-card-cta", cta);
        b.type = "button";
        b.disabled = ocupado;
        b.addEventListener("click", () => enviarMensaje(cta));
        card.appendChild(b);
      }
      if (card.childNodes.length) wrap.appendChild(card);
    });
    if (wrap.childNodes.length) {
      log.appendChild(wrap);
      bajarAlFinal();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", montar);
  } else {
    montar();
  }
})();
