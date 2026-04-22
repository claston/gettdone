from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.application import (
    ContactAttachment,
    ContactDeliveryError,
    ContactMessage,
    ContactProviderNotConfiguredError,
    ContactService,
    FileTooLargeError,
)
from app.dependencies import get_contact_service
from app.schemas import ContactResponse

router = APIRouter()


@router.post("/contact", response_model=ContactResponse, status_code=202)
async def send_contact(
    name: str = Form(...),
    email: str = Form(...),
    subject: str = Form(...),
    message: str = Form(...),
    attachment: UploadFile | None = File(default=None),
    contact_service: ContactService = Depends(get_contact_service),
) -> ContactResponse:
    clean_name = name.strip()
    clean_email = email.strip()
    clean_subject = subject.strip()
    clean_message = message.strip()

    if not clean_name or not clean_email or not clean_subject or not clean_message:
        raise HTTPException(status_code=400, detail="Fill in name, email, subject, and message.")

    if "@" not in clean_email:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")

    parsed_attachment: ContactAttachment | None = None
    if attachment and (attachment.filename or "").strip():
        attachment_raw = await attachment.read()
        parsed_attachment = ContactAttachment(
            filename=(attachment.filename or "attachment.bin").strip(),
            content_type=(attachment.content_type or "application/octet-stream").strip(),
            raw_bytes=attachment_raw,
        )

    try:
        result = await contact_service.deliver(
            ContactMessage(
                name=clean_name,
                email=clean_email,
                subject=clean_subject,
                message=clean_message,
                attachment=parsed_attachment,
            )
        )
    except FileTooLargeError:
        raise HTTPException(status_code=413, detail="Attachment exceeds maximum size of 2 MB.")
    except ContactProviderNotConfiguredError:
        raise HTTPException(status_code=503, detail="Contact channel not configured yet. Configure Resend and try again.")
    except ContactDeliveryError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to deliver contact message: {exc}")

    return ContactResponse(
        status="accepted",
        delivery_mode=result.delivery_mode,
        provider_message_id=result.provider_message_id,
    )
