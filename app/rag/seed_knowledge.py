"""Seed idempotente de la base de conocimiento (spec §3-4).

Uso: python -m app.rag.seed_knowledge
Carga conocimiento para los negocios del seed_demo ("Tienda Demo" y "Sereno").
Idempotente por (business_id, title): si el título ya existe, no lo duplica.
"""
from app.db.database import SessionLocal, init_db
from app.db.models import KNOWLEDGE_CATEGORIES, Business, Knowledge

# (categoría, título, contenido, keywords)
TIENDA_DEMO_KB = [
    (
        "horario",
        "Horario de atención",
        "Lunes a viernes de 9:00 a 20:00. Sábados de 10:00 a 15:00. Domingos y feriados cerrado.",
        "horario abre cierra hora horas día dias fin de semana sabado domingo",
    ),
    (
        "direccion",
        "Dirección del local",
        "Estamos en Av. Corrientes 1234, CABA, Buenos Aires, a dos cuadras del subte Congreso. Retiro en mostrador sin costo.",
        "direccion donde quedan local sucursal mapa llegar retiro",
    ),
    (
        "politica",
        "Política de envíos y devoluciones",
        "Envíos a todo el país por Andreani en 48-72 hs hábiles; en CABA se entregan en el día si el pedido es antes de las 14:00. El envío es gratis en compras mayores a $15.000. Cambios y devoluciones dentro de los 30 días con el producto sin abrir y con comprobante; el café ya abierto no se devuelve por normativa de alimentos.",
        "envio envios entrega devolucion devoluciones cambio cambios frete",
    ),
    (
        "faq",
        "¿Hacen envíos a todo el país?",
        "Sí, enviamos a todo el país por Andreani en 48-72 hs hábiles. En CABA entregamos el mismo día si el pedido se hace antes de las 14:00. En compras mayores a $15.000 el envío es gratis.",
        "envio envian pais correo entrega demora",
    ),
    (
        "faq",
        "¿Puedo pagar con transferencia o tarjeta?",
        "Sí. Aceptamos transferencia bancaria (MP y Galicia), tarjetas de crédito y débito (Visa, Mastercard, Amex) y efectivo en el local. Con transferencia tenés 10% de descuento.",
        "pago pagos tarjeta transferencia credito debito efectivo descuento",
    ),
    (
        "faq",
        "¿El café es de especialidad?",
        "Sí, trabajamos sólo café de especialidad tostado en micro-lote: origen único o mezclas propias, tueste medio, con notas de chocolate, frutos rojos o cítricos según la temporada.",
        "cafe grano tostado especialidad origen",
    ),
    (
        "faq",
        "¿Puedo retirar en el local?",
        "Sí, podés retirar gratis en Av. Corrientes 1234 comprando por web o por WhatsApp. Te avisamos cuando esté listo (suele tardar menos de 2 horas en horario hábil).",
        "retiro retiro retirar mostador local recoger",
    ),
    (
        "proceso",
        "Proceso de compra",
        "1) Elegís tus productos por web o WhatsApp. 2) Te confirmamos el stock y el total. 3) Elegís envío o retiro en local y pagás por transferencia o tarjeta. 4) Preparamos el pedido el mismo día. 5) Te enviamos el seguimiento o te avisamos para retiro.",
        "compra comprar pedido pasos como se compra orden",
    ),
    (
        "condiciones",
        "Condiciones de pago",
        "Precios en pesos argentinos e IVA incluido. Transferencia con 10% de descuento. Cuotas sin interés en hasta 3 con tarjetas seleccionadas. Pedidos online se abonan antes de despachar; en el local se puede pagar en cuotas con débito automático. Factura B o C emitida al momento de la compra; CUIT necesario para factura A.",
        "pago factura iva cuotas precio condiciones transferencia",
    ),
]

SERENO_KB = [
    (
        "horario",
        "Horario de soporte",
        "Lunes a viernes de 9:00 a 19:00 (ART). Soporte por WhatsApp y email; fuera de horario respondemos al siguiente día hábil. Clientes con plan mensual tienen prioridad de respuesta en menos de 4 horas hábiles.",
        "horario soporte atencion ayuda contacto hora",
    ),
    (
        "direccion",
        "Ubicación",
        "Equipo remoto con base en Buenos Aires, Argentina. Trabajamos 100% online con clientes de toda Latinoamérica; las reuniones son por videollamada (Google Meet o Zoom).",
        "direccion oficina donde sede remoto buenos aires",
    ),
    (
        "politica",
        "Política de soporte",
        "Soporte incluido en todos los planes: ajustes de contenido, dudas de uso y corrección de errores sin costo durante los primeros 30 días posteriores a la entrega. Cambios de diseño o funcionalidades nuevas se cotizan aparte. Tiempo de respuesta: 4 horas hábiles en días hábiles.",
        "soporte ayuda mantenimiento garantia error soporte tecnico",
    ),
    (
        "faq",
        "¿Cuánto tarda la entrega?",
        "La landing se entrega en 7 días hábiles y la landing con agente en 14 días hábiles desde que recibimos los contenidos y el 50% de seña. Los plazos se cuentan en días hábiles desde la aprobación del brief.",
        "tarda entrega plazo tiempo dias demora rapidez",
    ),
    (
        "faq",
        "¿Qué incluye el servicio?",
        "Incluye diseño y desarrollo de la landing (copy, formularios y dominio configurado), integración del chatbot IA con la base de conocimiento de tu negocio, agenda conectada a tu calendario, 1 mes de soporte y capacitación por videollamada de 1 hora.",
        "incluye incluye que sirve alcance servicio entregables",
    ),
    (
        "faq",
        "¿Hay mensualidad?",
        "Sí. Los planes son mensuales e incluyen hosting, mantenimiento, soporte y mejoras del asistente. Los primeros 3 meses se pueden pagar por adelantado; con ese pago la landing de tu negocio queda incluida sin costo adicional.",
        "mensualidad mensual precio cuota suscripcion abono plan",
    ),
    (
        "faq",
        "¿La landing está incluida?",
        "La landing de tu negocio está incluida cuando contratas el plan mensual y pagas los primeros 3 meses por adelantado. Incluye diseño, copy, formularios y dominio configurado. Si prefieres solo el chatbot en tu web actual, el plan mensual también sirve sin necesidad de landing nueva.",
        "landing incluida pagina web gratuita web propia incluye gratis",
    ),
    (
        "proceso",
        "Proceso de entrega por fases",
        "Fase 1 (día 1-2): kickoff y brief. Fase 2 (día 3-6): diseño de la landing para tu aprobación. Fase 3 (día 7-10): desarrollo e integración del chatbot con tu knowledge base. Fase 4 (día 11-14): pruebas, capacitación y puesta en producción. Aprobás cada fase antes de avanzar a la siguiente.",
        "proceso fases entrega pasos metodologia como trabajamos",
    ),
    (
        "condiciones",
        "Condiciones de contrato y cancelación",
        "Seña del 50% para iniciar; saldo contra entrega. Contrato de 12 meses para el plan mensual, cancelable con 30 días de aviso previo por escrito. Durante los primeros 30 días podés cancelar el plan mensual sin penalidad. El contenido y el dominio siempre son del cliente: si cancelás, te entregamos las credenciales. Pagos por transferencia o tarjeta; facturación con factura B o C.",
        "contrato cancelacion terminar condiciones seña contrato mensual",
    ),
]

KNOWLEDGE_BY_BUSINESS = {
    "Tienda Demo": TIENDA_DEMO_KB,
    "Sereno": SERENO_KB,
}


def seed_knowledge(db) -> int:
    """Carga conocimiento faltante. Devuelve la cantidad de docs creados."""
    valid_categories = set(KNOWLEDGE_CATEGORIES)
    created = 0
    for business_name, entries in KNOWLEDGE_BY_BUSINESS.items():
        biz = db.query(Business).filter(Business.name == business_name).first()
        if biz is None:
            continue
        for category, title, content, keywords in entries:
            if category not in valid_categories:
                raise ValueError(f"categoría inválida: {category}")
            exists = (
                db.query(Knowledge)
                .filter(Knowledge.business_id == biz.id, Knowledge.title == title)
                .first()
            )
            if exists:
                continue
            db.add(
                Knowledge(
                    business_id=biz.id,
                    category=category,
                    title=title,
                    content=content,
                    keywords=keywords,
                )
            )
            created += 1
    db.commit()
    return created


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        created = seed_knowledge(db)
        print(f"seed knowledge ok: {created} docs nuevos")
    finally:
        db.close()


if __name__ == "__main__":
    main()
