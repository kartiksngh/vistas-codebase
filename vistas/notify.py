"""
Email a Vistas offline deck (self-contained interactive HTML) to a recipient.

The Terminal v2 deck is ~30 MB — over Gmail's 25 MB attachment cap — so by default
the deck is ZIPPED before attaching (a ~30 MB HTML compresses to ~5-6 MB). The
recipient unzips once and opens the .html in any browser: fully interactive, offline,
no server. If the (zipped) attachment still exceeds the limit, the email is sent with
a link to the published deck instead of the attachment.

Config is via environment variables ONLY (never hard-code a password):
  VISTAS_SMTP_HOST   default smtp.gmail.com
  VISTAS_SMTP_PORT   default 587   (STARTTLS)
  VISTAS_SMTP_USER   the sending Gmail address
  VISTAS_SMTP_PASS   a Gmail *App Password* (16 chars; create at
                     https://myaccount.google.com/apppasswords — needs 2FA on)
  VISTAS_EMAIL_TO    default recipient (falls back to VISTAS_SMTP_USER)
  VISTAS_PUBLISH_URL optional public deck URL, used as a fallback link

Never raises — returns a status dict so a caller (CLI / Flask) degrades gracefully.
"""
from __future__ import annotations

import os
import ssl
import zipfile
import smtplib
from email.message import EmailMessage

GMAIL_LIMIT = 25 * 1024 * 1024          # 25 MB Gmail hard cap
ATTACH_LIMIT = 24 * 1024 * 1024         # leave headroom for MIME base64 overhead


def zip_deck(html_path: str, out_path: str | None = None) -> str:
    """Zip a deck HTML to a .zip beside it (deflate). Returns the .zip path."""
    out_path = out_path or (os.path.splitext(html_path)[0] + ".zip")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(html_path, arcname=os.path.basename(html_path))
    return out_path


def email_deck(to: str | None = None, deck_path: str | None = None,
               subject: str | None = None, body: str | None = None,
               zip_if_large: bool = True) -> dict:
    """Email the given deck (default: the newest Terminal v2 deck). Zips it when large.
    Returns {ok, ...}; never raises."""
    try:
        host = os.environ.get("VISTAS_SMTP_HOST", "smtp.gmail.com")
        port = int(os.environ.get("VISTAS_SMTP_PORT", "587"))
        user = os.environ.get("VISTAS_SMTP_USER")
        pw = os.environ.get("VISTAS_SMTP_PASS")
        to = to or os.environ.get("VISTAS_EMAIL_TO") or user
        if not user or not pw:
            return {"ok": False, "error": "Set VISTAS_SMTP_USER + VISTAS_SMTP_PASS "
                    "(a Gmail App Password) to send email."}
        if not to:
            return {"ok": False, "error": "No recipient (set VISTAS_EMAIL_TO)."}

        if deck_path is None:
            from . import deck as _deck
            deck_path = os.path.join(_deck.OUTPUT_DIR, _deck.TERMINAL_LATEST)
        if not os.path.exists(deck_path):
            return {"ok": False, "error": f"Deck not found: {deck_path}"}

        attach_path, attach_name, attached = deck_path, os.path.basename(deck_path), True
        size = os.path.getsize(deck_path)
        if zip_if_large and size > ATTACH_LIMIT:
            attach_path = zip_deck(deck_path)
            attach_name = os.path.basename(attach_path)
            size = os.path.getsize(attach_path)

        msg = EmailMessage()
        msg["From"] = user
        msg["To"] = to
        msg["Subject"] = subject or "Vistas — your offline terminal deck"
        pub = os.environ.get("VISTAS_PUBLISH_URL", "")
        default_body = ("Attached is your Vistas terminal deck — a self-contained, fully "
                        "interactive HTML file. Unzip it (if zipped) and open in any browser: "
                        "no server, no internet needed. Re-pick indices, change the window, "
                        "switch Performance/Valuation tabs — it recomputes in the browser.\n")
        msg.set_content((body or default_body) + (f"\nLive link: {pub}\n" if pub else ""))

        if size <= ATTACH_LIMIT:
            with open(attach_path, "rb") as f:
                data = f.read()
            maintype, subtype = ("application", "zip") if attach_name.endswith(".zip") else ("text", "html")
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=attach_name)
        else:                                  # too big even zipped -> send the link only
            attached = False
            link = pub or "(set VISTAS_PUBLISH_URL)"
            msg.set_content(f"The deck ({size // (1024*1024)} MB) is too large to email even "
                            f"zipped. Open the live version instead:\n{link}\n")

        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=ctx)
            s.login(user, pw)
            s.send_message(msg)
        return {"ok": True, "to": to, "attached": attached, "file": attach_name,
                "size_mb": round(size / 1e6, 1)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    import json
    print(json.dumps(email_deck(), indent=2))
