"""Seed demo idempotente: 1 negocio + 5 productos. Uso: python -m app.db.seed_demo"""
from app.db.database import SessionLocal, init_db
from app.db.models import Business, Product

PRODUCTS = [
    ("Café de especialidad 1kg", "Café en grano tostado medio, notas de chocolate.", 1850),
    ("Taza de cerámica artesanal", "Taza de 350ml hecha a mano.", 950),
    ("Cafetera prensa francesa", "Prensa francesa de 1 litro en acero y vidrio.", 3900),
    ("Suscripción café mensual", "1kg de café de temporada cada mes a domicilio.", 1600),
    ("Curso de barismo básico", "Taller presencial de 3 horas, incluye materiales.", 4500),
]

SERENO_PLANS = [
    ("Recepción 24/7", "Ana contesta consultas 24/7 con la base de conocimiento de tu negocio, clasifica cada lead y te pasa solo los casos calientes. Con los primeros 3 meses por adelantado se incluye la landing de tu negocio.", 14900),
    ("Recepción + Closer", "Todo lo del plan Recepción 24/7, más calificación, agenda en tu calendario y seguimiento automático a los 3 días.", 29900),
    ("Chatbot IA mensual", "Chatbot con memoria de cliente y agenda, para tu web o la que armemos. Soporte incluido.", 9900),
]


def _seed_business(db, name, description, products, persona="consultiva"):
    biz = db.query(Business).filter_by(name=name).first()
    if not biz:
        biz = Business(name=name, description=description, tone="cálido y cercano",
                       persona=persona)
        db.add(biz)
        db.commit()
        db.refresh(biz)
    if not db.query(Product).filter_by(business_id=biz.id).first():
        for pname, desc, cents in products:
            db.add(Product(business_id=biz.id, name=pname, description=desc, price_cents=cents))
        db.commit()
    return biz


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        b1 = _seed_business(db, "Tienda Demo", "Tienda de café de especialidad y accesorios.",
                            PRODUCTS, persona="cercana")
        b2 = _seed_business(
            db, "Sereno",
            "Landing pages + chatbot de IA para negocios pequeños y medianos.",
            SERENO_PLANS,
            persona="experta",
        )
        print(f"seed ok tienda={b1.id} sereno={b2.id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
