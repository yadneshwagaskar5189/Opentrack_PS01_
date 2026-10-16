"""Real WhatsApp delivery for approved messages (optional).

WHATSAPP_PROVIDER in .env picks how approved messages leave the building:
  off     (default) nothing is sent; the outbox marks them 'simulated'
  twilio  Twilio WhatsApp Sandbox or a Twilio WhatsApp sender
  meta    Meta WhatsApp Cloud API (graph.facebook.com)

SAFETY: the student phone numbers in seed_data are made up and may belong to real
strangers. So real sending ONLY goes to:
  WHATSAPP_REDIRECT_TO=+919876543210   every message goes to this phone instead, with a
                                       first line saying who it was meant for (demo mode), or
  WHATSAPP_ALLOWED=+9198...,+9199...   only these numbers get real messages; others stay simulated.
With neither set, nothing real is sent.

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


def mode() -> dict:
    """What delivery will do right now (shown to staff before they approve)."""
    provider = config.WHATSAPP_PROVIDER
    if provider not in ("twilio", "meta"):
        return {"provider": "off", "real": False, "note": "Delivery is simulated: messages are only logged."}
    if config.WHATSAPP_REDIRECT_TO:
        return {"provider": provider, "real": True, "redirect_to": _e164(config.WHATSAPP_REDIRECT_TO),
                "note": f"Demo mode: every message is really sent, but to {_e164(config.WHATSAPP_REDIRECT_TO)}."}
    if config.WHATSAPP_ALLOWED:
        return {"provider": provider, "real": True,
                "note": f"Only {len(config.WHATSAPP_ALLOWED)} allowed number(s) get real messages; others are simulated."}
    return {"provider": provider, "real": False,
            "note": "WhatsApp is configured but no WHATSAPP_REDIRECT_TO or WHATSAPP_ALLOWED is set, so nothing real is sent."}


def send(phone: str, body: str, name: str = "") -> str:
    """Send one message. Returns the outbox status: simulated | sent ... | not sent ... | failed: ..."""
    provider = config.WHATSAPP_PROVIDER
    if provider not in ("twilio", "meta"):
        return "simulated"
    to = phone
    if config.WHATSAPP_REDIRECT_TO:
        to = config.WHATSAPP_REDIRECT_TO
        body = f"[Demo: for {name or 'student'}, {_e164(phone)}]\n{body}"
    elif _digits(_e164(phone)) not in {_digits(_e164(p)) for p in config.WHATSAPP_ALLOWED}:
        return "simulated (not on the allowed list)"
    try:
        msg_id = _twilio(to, body) if provider == "twilio" else _meta(to, body)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        return f"failed: {provider} said {e.code} {_short_error(detail)}"
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
