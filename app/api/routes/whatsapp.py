"""WhatsApp — Integración con Twilio para enviar y recibir mensajes."""
import os
from fastapi import APIRouter, Request, HTTPException
from app.config import settings

router = APIRouter()


@router.post("/whatsapp/send")
async def send_whatsapp(request: Request):
    """Envía un mensaje de WhatsApp usando Twilio."""
    data = await request.json()
    to = data.get("to", "")
    message = data.get("message", "")
    
    if not to or not message:
        raise HTTPException(status_code=400, detail="Faltan parámetros")
    
    # Aquí iría la integración real con Twilio
    # Por ahora, devolvemos éxito simulado
    print(f"[WhatsApp] Enviando a {to}: {message}")
    
    return {"status": "sent", "to": to}


@router.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request):
    """Recibe mensajes de WhatsApp desde Twilio."""
    form = await request.form_data()
    
    from_number = form.get("From", "").replace("whatsapp:", "")
    body = form.get("Body", "")
    
    print(f"[WhatsApp] Mensaje recibido de {from_number}: {body}")
    
    # Aquí iría la lógica para procesar el mensaje con el agente
    # y enviar respuesta
    
    return {"status": "received"}
