import os
from typing import Optional

from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType


class EmailSettings:
    MAIL_USERNAME: str = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD: str = os.getenv("MAIL_PASSWORD", "")
    MAIL_FROM: str = os.getenv("MAIL_FROM", "")
    MAIL_PORT: int = int(os.getenv("MAIL_PORT", "587"))
    MAIL_SERVER: str = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_FROM_NAME: str = os.getenv("MAIL_FROM_NAME", "Grepp")
    MAIL_STARTTLS: bool = os.getenv("MAIL_STARTTLS", "true").lower() == "true"
    MAIL_SSL_TLS: bool = os.getenv("MAIL_SSL_TLS", "false").lower() == "true"
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "")


email_settings = EmailSettings()


def get_mail_config() -> Optional[ConnectionConfig]:
    if not email_settings.MAIL_USERNAME or not email_settings.MAIL_PASSWORD:
        return None

    return ConnectionConfig(
        MAIL_USERNAME=email_settings.MAIL_USERNAME,
        MAIL_PASSWORD=email_settings.MAIL_PASSWORD,
        MAIL_FROM=email_settings.MAIL_FROM,
        MAIL_PORT=email_settings.MAIL_PORT,
        MAIL_SERVER=email_settings.MAIL_SERVER,
        MAIL_FROM_NAME=email_settings.MAIL_FROM_NAME,
        MAIL_STARTTLS=email_settings.MAIL_STARTTLS,
        MAIL_SSL_TLS=email_settings.MAIL_SSL_TLS,
        USE_CREDENTIALS=True,
        VALIDATE_CERTS=True,
    )


async def send_password_reset_email(email: str, token: str, firstname: Optional[str] = None) -> bool:
    config = get_mail_config()
    if not config:
        print(f"[DEV MODE] Password reset link: {email_settings.FRONTEND_URL}/reset-password?token={token}")
        return True

    reset_url = f"{email_settings.FRONTEND_URL}/reset-password?token={token}"
    greeting = f"Hej {firstname}" if firstname else "Hej"

    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #505654;">Återställ ditt lösenord</h2>
            <p>{greeting},</p>
            <p>Du har begärt att återställa ditt lösenord för ditt Greppmästerskaps-konto.</p>
            <p>Klicka på knappen nedan för att välja ett nytt lösenord:</p>
            <p style="text-align: center; margin: 30px 0;">
                <a href="{reset_url}"
                   style="background-color: #505654; color: white; padding: 12px 24px;
                          text-decoration: none; border-radius: 6px; display: inline-block;">
                    Återställ lösenord
                </a>
            </p>
            <p>Eller kopiera och klistra in denna länk i din webbläsare:</p>
            <p style="word-break: break-all; color: #666; font-size: 14px;">{reset_url}</p>
            <p><strong>Länken är giltig i 1 timme.</strong></p>
            <p>Om du inte begärde detta kan du ignorera detta mail.</p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
            <p style="color: #999; font-size: 12px;">Detta mail skickades från Grepp.</p>
        </div>
    </body>
    </html>
    """

    message = MessageSchema(
        subject="Återställ ditt lösenord - Grepp",
        recipients=[email],
        body=html_content,
        subtype=MessageType.html,
    )

    try:
        fm = FastMail(config)
        await fm.send_message(message)
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False
