"""Real WhatsApp delivery for approved messages (optional).

WHATSAPP_PROVIDER in .env picks how approved messages leave the building:
  off     (default) nothing is sent; the outbox marks them 'simulated'
  link    free, no account needed: each approved message gets a "Send on WhatsApp" button that
          opens WhatsApp (web or app) with the text already typed; staff press Send
  twilio  Twilio WhatsApp Sandbox or a Twilio WhatsApp sender (a Twilio TRIAL account can only send
          Twilio's own templates, so custom reminders fail with "ContentSid Required")
  meta    Meta WhatsApp Cloud API (graph.facebook.com)
Whenever a message is not sent automatically, it still gets a "Send on WhatsApp" link (same safety rule).

WHO GETS A MESSAGE (the same rule for automatic sending and for Send on WhatsApp buttons):
  1. Students added through the app (Add data or the copilot): their own number. Staff typed it in,
     so it is real (students.real_contact = 1). Nothing to configure.
  2. Numbers listed in WHATSAPP_ALLOWED=+9198...,+9199...: their own number.
  3. Everyone else, i.e. the sample students imported from seed_data, whose numbers are made up and
     may belong to real strangers: WHATSAPP_REDIRECT_TO=+919876543210 gets the message instead, with
     a first line saying who it was meant for (demo mode). Without a redirect it stays simulated.

WhatsApp only lets a business send free-text messages to someone who messaged it in the
last 24 hours. With the Twilio Sandbox, sending "join <code>" from the phone counts.
"""
import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import config

TIMEOUT_S = 10


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _e164(phone: str) -> str:
    d = _digits(phone)
    if len(d) == 10:
        d = "91" + d
    return "+" + d


def _allowed(phone: str) -> bool:
    return _digits(_e164(phone)) in {_digits(_e164(p)) for p in config.WHATSAPP_ALLOWED}


def target(phone: str, body: str, name: str = "", trusted: bool = False) -> tuple[str, str] | None:
    """Who really gets this message, and the text they get. None = nobody (stays simulated).
    trusted = the student's number was typed in by staff (added through the app)."""
    if trusted or _allowed(phone):
        return phone, body
    if config.WHATSAPP_REDIRECT_TO:
        return config.WHATSAPP_REDIRECT_TO, f"[Demo: for {name or 'student'}, {_e164(phone)}]\n{body}"
    return None


def link(phone: str, body: str, name: str = "", trusted: bool = False) -> dict | None:
    """A wa.me click-to-chat link: opens WhatsApp with the message typed, a person presses Send."""
    t = target(phone, body, name, trusted)
    if not t:
        return None
    to, text = t
    return {"to": _e164(to), "url": f"https://wa.me/{_digits(_e164(to))}?text={urllib.parse.quote(text)}"}


def mode() -> dict:
    """What delivery will do right now (shown to staff before they approve)."""
    provider = config.WHATSAPP_PROVIDER
    redirect = _e164(config.WHATSAPP_REDIRECT_TO) if config.WHATSAPP_REDIRECT_TO else None
    others = (f"sample students' messages go to {redirect} (demo mode)" if redirect
              else "sample students (made-up numbers) stay simulated")
    if provider not in ("link", "twilio", "meta"):
        return {"provider": "off", "real": False, "note": "Delivery is simulated: messages are only logged."}
    how = ("After approval, each message gets a Send on WhatsApp button" if provider == "link"
           else f"Messages are sent automatically through {provider.title()}")
    return {"provider": provider, "real": True, **({"redirect_to": redirect} if redirect else {}),
            "note": f"{how}. Students added in the app get it on their own number; {others}."}


def send(phone: str, body: str, name: str = "", trusted: bool = False) -> str:
    """Send one message. Returns the outbox status: simulated | ready ... | sent ... | failed: ..."""
    provider = config.WHATSAPP_PROVIDER
    if provider == "link":
        return ("ready: press Send on WhatsApp" if target(phone, body, name, trusted)
                else "simulated (sample number: set WHATSAPP_REDIRECT_TO)")
    if provider not in ("twilio", "meta"):
        return "simulated"
    t = target(phone, body, name, trusted)
    if not t:
        return "simulated (sample number: set WHATSAPP_REDIRECT_TO)"
    to, body = t
    try:
        msg_id = _twilio(to, body) if provider == "twilio" else _meta(to, body)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        short = _short_error(detail)
        if "contentsid" in short.lower():
            short += " (a Twilio trial account only sends Twilio's own templates; use WHATSAPP_PROVIDER=link)"
        return f"failed: {provider} said {e.code} {short}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return f"failed: could not reach {provider} ({e})"
    where = f" to {_e164(to)}" if to != phone else ""
    return f"sent{where} ({msg_id})" if msg_id else f"sent{where}"


def _short_error(detail: str) -> str:
    try:
        d = json.loads(detail)
        return str(d.get("message") or d.get("error", {}).get("message") or detail)[:200]
    except (ValueError, AttributeError):
        return detail[:200]


def _twilio(to: str, body: str) -> str:
    sid, token = config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN
    if not sid or not token:
        raise urllib.error.URLError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN missing in .env")
    data = urllib.parse.urlencode({"From": config.TWILIO_WHATSAPP_FROM, "To": f"whatsapp:{_e164(to)}",
                                   "Body": body[:1500]}).encode()
    req = urllib.request.Request(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data=data,
                                 headers={"Authorization": "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode(),
                                          "Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": "institute-copilot/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read().decode()).get("sid", "")


def _meta(to: str, body: str) -> str:
    token, number_id = config.META_WA_TOKEN, config.META_WA_PHONE_NUMBER_ID
    if not token or not number_id:
        raise urllib.error.URLError("META_WA_TOKEN / META_WA_PHONE_NUMBER_ID missing in .env")
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": _digits(_e164(to)),
               "type": "text", "text": {"body": body[:4000]}}
    req = urllib.request.Request(f"https://graph.facebook.com/{config.META_GRAPH_VERSION}/{number_id}/messages",
                                 data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                          "User-Agent": "institute-copilot/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        out = json.loads(r.read().decode())
        return (out.get("messages") or [{}])[0].get("id", "")
