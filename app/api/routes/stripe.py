"""Stripe — Checkout y webhooks para gestionar pagos y suscripciones."""
import os
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
from app.config import settings
from app.db.database import get_db
from app.db.models import Business

router = APIRouter()

# Precios por plan (estos IDs vienen de tu dashboard de Stripe)
PLAN_PRICES = {
    "starter": os.getenv("STRIPE_PRICE_STARTER", "price_xxx"),
    "pro": os.getenv("STRIPE_PRICE_PRO", "price_xxx"),
    "enterprise": os.getenv("STRIPE_PRICE_ENTERPRISE", "price_xxx"),
}


@router.post("/stripe/checkout")
async def create_checkout(request: Request, db: Session = Depends(get_db)):
    """Crea una sesión de Stripe Checkout para un plan."""
    data = await request.json()
    plan = data.get("plan", "starter")
    business_token = data.get("business_token", "")
    
    if plan not in PLAN_PRICES:
        raise HTTPException(status_code=400, detail="Plan inválido")
    
    # Aquí crearías la sesión de Stripe Checkout
    # Por ahora, devolvemos una URL simulada
    checkout_url = f"https://checkout.stripe.com/pay/cs_test_mock_{plan}"
    
    return {
        "checkout_url": checkout_url,
        "plan": plan,
    }


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Maneja eventos de Stripe (pagos, suscripciones, etc.)."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    
    # Verificar firma del webhook (en producción)
    # event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    
    try:
        event = await request.json()
    except:
        raise HTTPException(status_code=400, detail="Payload inválido")
    
    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})
    
    if event_type == "checkout.session.completed":
        # Pago exitoso — activar o crear suscripción
        customer_email = data.get("customer_email", "")
        metadata = data.get("metadata", {})
        plan = metadata.get("plan", "starter")
        business_token = metadata.get("business_token", "")
        
        print(f"[Stripe] Pago completado: {customer_email} - Plan: {plan}")
        
        # Aquí activarías la suscripción en tu DB
        
    elif event_type == "customer.subscription.deleted":
        # Suscripción cancelada — desactivar acceso
        customer_id = data.get("customer", "")
        print(f"[Stripe] Suscripción cancelada: {customer_id}")
    
    return {"status": "ok"}
