/* app.js — lógica de la landing: clave pública del tenant, catálogo
 * (/api/catalog) y formulario de captación (/api/leads).
 * Sin dependencias ni CDNs; el contenido remoto se pinta con textContent. */
(function () {
  "use strict";

  const LS_KEY = "av_key";

  const MSG = {
    vacio: "Déjanos al menos tu nombre, email o teléfono para poder contactarte.",
    offline:
      "El formulario no está disponible ahora. Escribinos desde el contacto del pie de página.",
    rate: "Recibimos varios envíos seguidos. Esperá un minuto y volvé a intentar.",
    error: "No pudimos enviar tu mensaje. Probá de nuevo en unos minutos.",
    sinRed: "Sin conexión. Revisá tu internet y volvé a intentar.",
    ok: "¡Listo! Recibimos tu mensaje y te contactamos a la brevedad.",
    invalid: "Revisá los datos e intentá de nuevo.",
  };

  function clean(v) {
    return typeof v === "string" ? v.trim() : "";
  }

  function read(k) {
    try {
      return localStorage.getItem(k) || "";
    } catch (e) {
      return "";
    }
  }

  function store(k, v) {
    try {
      localStorage.setItem(k, v);
    } catch (e) {
      /* almacenamiento no disponible: la página sigue funcionando */
    }
  }

  function nodo(tag, cls, texto) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (texto) n.textContent = texto;
    return n;
  }

  /* Prioridad: ?key= (explícita en la URL) → clave inyectada → recordada. */
  function resolverClave() {
    let url = "";
    try {
      url = clean(new URLSearchParams(location.search).get("key"));
    } catch (e) {
      url = "";
    }
    if (url) {
      store(LS_KEY, url);
      return url;
    }
    const global = clean(window.AGENT_VENTAS_KEY);
    if (global) return global;
    return clean(read(LS_KEY));
  }

  const publicKey = resolverClave();
  if (publicKey) {
    // widget.js se ejecuta después (mismo orden de <script>) y lo lee acá.
    window.AGENT_VENTAS_KEY = publicKey;
  }

  function txt(id) {
    const n = document.getElementById(id);
    return n ? n.value.trim() : "";
  }

  /* --- Catálogo del negocio ------------------------------------------- */

  function cargarCatalogo() {
    if (!publicKey) return;
    fetch("/api/catalog?public_key=" + encodeURIComponent(publicKey))
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) pintarCatalogo(data);
      })
      .catch(() => {
        /* Sin catálogo la landing sigue en pie. */
      });
  }

  function pintarCatalogo(data) {
    const bloque = document.getElementById("catalogo");
    if (!bloque) return;
    const planes = Array.isArray(data.plans) ? data.plans : [];
    const servicios = Array.isArray(data.services) ? data.services : [];
    if (!planes.length && !servicios.length) return;

    const biz =
      data.business && typeof data.business === "object" ? data.business : {};
    const titulo = document.getElementById("catalogo-titulo");
    if (titulo && clean(biz.name)) {
      titulo.textContent = "Planes de " + clean(biz.name);
    }
    rellenarLista(document.getElementById("catalogo-planes"), planes);
    if (servicios.length) {
      rellenarLista(document.getElementById("catalogo-servicios"), servicios);
      const sec = document.getElementById("catalogo-servicios-bloque");
      if (sec) sec.hidden = false;
    }
    bloque.hidden = false;
    pintarContactoPie(biz);
  }

  function rellenarLista(lista, items) {
    if (!lista) return;
    items.slice(0, 12).forEach((p) => {
      if (!p || typeof p !== "object") return;
      const li = nodo("li", "catalogo-item");
      if (clean(p.name)) li.appendChild(nodo("h4", "", clean(p.name)));
      if (clean(p.description)) {
        li.appendChild(nodo("p", "catalogo-desc", clean(p.description)));
      }
      if (clean(p.price_label)) {
        li.appendChild(nodo("p", "catalogo-precio", clean(p.price_label)));
      }
      lista.appendChild(li);
    });
  }

  function pintarContactoPie(biz) {
    const campos = [
      ["footer-horario", "Horario: ", biz.hours],
      ["footer-telefono", "Teléfono: ", biz.phone],
      ["footer-whatsapp", "WhatsApp: ", biz.whatsapp],
    ];
    campos.forEach((campo) => {
      const n = document.getElementById(campo[0]);
      const valor = clean(campo[2]);
      if (n && valor) {
        n.textContent = campo[1] + valor;
        n.hidden = false;
      }
    });
  }

  /* --- Formulario de captación ----------------------------------------- */

  function configurarFormulario() {
    const form = document.getElementById("form-lead");
    if (!form) return;
    const estado = document.getElementById("form-estado");
    const btn = document.getElementById("form-enviar");

    function setEstado(mensaje, ok) {
      estado.textContent = mensaje;
      estado.className = "form-estado " + (ok ? "is-ok" : "is-error");
    }

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const payload = {
        public_key: publicKey,
        name: txt("f-nombre"),
        company: txt("f-empresa"),
        email: txt("f-email"),
        phone: txt("f-telefono"),
        need: "",
        message: txt("f-mensaje"),
      };
      estado.className = "form-estado";
      estado.textContent = "";

      if (!payload.name && !payload.email && !payload.phone) {
        setEstado(MSG.vacio, false);
        return;
      }
      if (!payload.public_key) {
        setEstado(MSG.offline, false);
        return;
      }

      const textoPrevio = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Enviando…";
      try {
        const r = await fetch("/api/leads", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (r.status === 201) {
          setEstado(MSG.ok, true);
          form.reset();
        } else if (r.status === 401) {
          setEstado(MSG.offline, false);
        } else if (r.status === 429) {
          setEstado(MSG.rate, false);
        } else if (r.status === 422) {
          const det = await r.json().catch(() => null);
          setEstado(
            det && det.detail === "contact_required" ? MSG.vacio : MSG.invalid,
            false
          );
        } else {
          setEstado(MSG.error, false);
        }
      } catch (e) {
        setEstado(MSG.sinRed, false);
      } finally {
        btn.disabled = false;
        btn.textContent = textoPrevio;
      }
    });
  }

  cargarCatalogo();
  configurarFormulario();
})();
