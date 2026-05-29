from __future__ import annotations

import csv
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parent
STATIC_DIR = ROOT / "static"
DATA_DIR = Path(os.getenv("AGENDA_DATA_DIR") or ("/tmp/agenda-data" if os.getenv("VERCEL") else ROOT / "data"))
BOOKINGS_CSV = DATA_DIR / "agendamentos.csv"
SESSIONS_JSON = DATA_DIR / "whatsapp_sessions.json"
CONFIG_JSON = ROOT / "config.json"

CSV_FIELDS = [
    "id",
    "created_at",
    "updated_at",
    "name",
    "phone",
    "service",
    "date",
    "time",
    "notes",
    "source",
    "status",
    "cancelled_at",
    "reminder_sent_at",
]

DEFAULT_CONFIG: dict[str, Any] = {
    "business_name": "Agenda Facil",
    "timezone": "America/Sao_Paulo",
    "slot_minutes": 30,
    "min_notice_hours": 2,
    "max_days_ahead": 60,
    "services": [
        {"name": "Consulta", "duration_minutes": 30},
        {"name": "Retorno", "duration_minutes": 30},
        {"name": "Avaliacao", "duration_minutes": 60},
        {"name": "Atendimento online", "duration_minutes": 30},
    ],
    "breaks": [["12:00", "13:00"]],
    "unavailable_dates": [],
    "hours": {
        "monday": ["09:00", "18:00"],
        "tuesday": ["09:00", "18:00"],
        "wednesday": ["09:00", "18:00"],
        "thursday": ["09:00", "18:00"],
        "friday": ["09:00", "18:00"],
        "saturday": ["09:00", "13:00"],
        "sunday": None,
    },
}


def load_config() -> dict[str, Any]:
    if CONFIG_JSON.exists():
        with CONFIG_JSON.open("r", encoding="utf-8") as f:
            custom = json.load(f)
        merged = DEFAULT_CONFIG | custom
        merged["hours"] = DEFAULT_CONFIG["hours"] | custom.get("hours", {})
        merged["services"] = normalize_services(merged.get("services", []))
        return merged
    config = DEFAULT_CONFIG.copy()
    config["services"] = normalize_services(config["services"])
    return config


def normalize_services(services: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for service in services:
        if isinstance(service, str):
            normalized.append({"name": service, "duration_minutes": DEFAULT_CONFIG["slot_minutes"]})
        elif isinstance(service, dict) and service.get("name"):
            normalized.append(
                {
                    "name": str(service["name"]),
                    "duration_minutes": int(service.get("duration_minutes") or DEFAULT_CONFIG["slot_minutes"]),
                }
            )
    return normalized


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    public = config.copy()
    public["services"] = [service["name"] for service in config["services"]]
    public["service_details"] = config["services"]
    return public


def ensure_data_files() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if not BOOKINGS_CSV.exists():
        with BOOKINGS_CSV.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()
    else:
        with BOOKINGS_CSV.open("r", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        needs_migration = bool(rows) and set(rows[0].keys()) != set(CSV_FIELDS)
        if not rows:
            with BOOKINGS_CSV.open("r", newline="", encoding="utf-8") as f:
                header = next(csv.reader(f), [])
            needs_migration = header != CSV_FIELDS
        if needs_migration:
            with BOOKINGS_CSV.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                writer.writeheader()
                for row in rows:
                    writer.writerow(normalized_row(row))
    if not SESSIONS_JSON.exists():
        SESSIONS_JSON.write_text("{}", encoding="utf-8")


def load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length) if length else b"{}"
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def read_form_body(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length).decode("utf-8") if length else ""
    parsed = urllib.parse.parse_qs(raw)
    return {key: values[0] for key, values in parsed.items() if values}


def send_json(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_json_with_headers(
    handler: BaseHTTPRequestHandler,
    payload: Any,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
    if headers:
        for key, value in headers.items():
            handler.send_header(key, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_text(
    handler: BaseHTTPRequestHandler,
    body: str,
    status: int = 200,
    content_type: str = "text/plain; charset=utf-8",
) -> None:
    encoded = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def cookie_value(handler: BaseHTTPRequestHandler, name: str) -> str:
    cookie_header = handler.headers.get("Cookie", "")
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        key, value = part.strip().split("=", 1)
        if key == name:
            return value
    return ""


def admin_secret() -> str:
    return os.getenv("ADMIN_SECRET") or os.getenv("ADMIN_PASSWORD") or "admin123"


def admin_password() -> str:
    return os.getenv("ADMIN_PASSWORD", "admin123")


def sign_value(value: str) -> str:
    digest = hmac.new(admin_secret().encode("utf-8"), value.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def make_admin_session() -> str:
    issued_at = str(int(datetime.now().timestamp()))
    nonce = secrets.token_urlsafe(16)
    payload = f"{issued_at}.{nonce}"
    return f"{payload}.{sign_value(payload)}"


def valid_admin_session(token: str) -> bool:
    if not token or token.count(".") != 2:
        return False
    issued_at, nonce, signature = token.split(".", 2)
    payload = f"{issued_at}.{nonce}"
    if not hmac.compare_digest(signature, sign_value(payload)):
        return False
    try:
        age = datetime.now().timestamp() - int(issued_at)
    except ValueError:
        return False
    return 0 <= age <= 60 * 60 * 12


def is_admin(handler: BaseHTTPRequestHandler) -> bool:
    return valid_admin_session(cookie_value(handler, "admin_session"))


def require_admin(handler: BaseHTTPRequestHandler) -> bool:
    if is_admin(handler):
        return True
    send_json(handler, {"error": "Login necessario."}, HTTPStatus.UNAUTHORIZED)
    return False


def weekday_key(target: date) -> str:
    return [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ][target.weekday()]


def parse_clock(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def normalized_row(row: dict[str, Any]) -> dict[str, str]:
    return {field: str(row.get(field, "") or "") for field in CSV_FIELDS}


def list_bookings(raw: bool = False) -> list[dict[str, str]]:
    ensure_data_files()
    with BOOKINGS_CSV.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if raw:
        return rows
    return [normalized_row(row) for row in rows]


def write_bookings(rows: list[dict[str, Any]]) -> None:
    ensure_data_files()
    with BOOKINGS_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(normalized_row(row))


def find_booking(booking_id: str) -> dict[str, str] | None:
    for row in list_bookings():
        if row["id"] == booking_id:
            return row
    return None


def update_booking(booking_id: str, changes: dict[str, str]) -> dict[str, str] | None:
    rows = list_bookings()
    updated: dict[str, str] | None = None
    for index, row in enumerate(rows):
        if row["id"] == booking_id:
            row.update({key: str(value) for key, value in changes.items() if key in CSV_FIELDS})
            row["updated_at"] = datetime.now().isoformat(timespec="seconds")
            rows[index] = normalized_row(row)
            updated = rows[index]
            break
    if updated:
        write_bookings(rows)
    return updated


def append_booking(payload: dict[str, str]) -> dict[str, str]:
    ensure_data_files()
    row = {
        "id": secrets.token_hex(6),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": "",
        "name": payload.get("name", "").strip(),
        "phone": payload.get("phone", "").strip(),
        "service": payload.get("service", "").strip(),
        "date": payload.get("date", "").strip(),
        "time": payload.get("time", "").strip(),
        "notes": payload.get("notes", "").strip(),
        "source": payload.get("source", "site").strip(),
        "status": payload.get("status", "confirmed").strip(),
        "cancelled_at": "",
        "reminder_sent_at": "",
    }
    with BOOKINGS_CSV.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)
    return row


def validate_booking(payload: dict[str, str]) -> tuple[bool, str]:
    required = ["name", "phone", "service", "date", "time"]
    missing = [field for field in required if not str(payload.get(field, "")).strip()]
    if missing:
        return False, f"Campos obrigatorios: {', '.join(missing)}"
    try:
        target = datetime.strptime(payload["date"], "%Y-%m-%d").date()
        parse_clock(payload["time"])
    except ValueError:
        return False, "Data ou horario invalido."
    if target < date.today():
        return False, "Escolha uma data futura."
    config = load_config()
    max_days = int(config.get("max_days_ahead") or 0)
    if max_days and target > date.today() + timedelta(days=max_days):
        return False, f"Escolha uma data em ate {max_days} dias."
    slots = available_slots(target, payload.get("service", ""), payload.get("id", ""))
    if payload["time"] not in slots:
        return False, "Horario indisponivel."
    return True, ""


def service_duration(service_name: str) -> int:
    config = load_config()
    for service in config["services"]:
        if service["name"] == service_name:
            return int(service["duration_minutes"])
    return int(config["slot_minutes"])


def intervals_overlap(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a


def crosses_break(start: datetime, end: datetime, target: date, breaks: list[list[str]]) -> bool:
    for break_range in breaks:
        if len(break_range) != 2:
            continue
        break_start = datetime.combine(target, parse_clock(break_range[0]))
        break_end = datetime.combine(target, parse_clock(break_range[1]))
        if intervals_overlap(start, end, break_start, break_end):
            return True
    return False


def available_slots(target: date, service_name: str = "", ignore_booking_id: str = "") -> list[str]:
    config = load_config()
    if target.isoformat() in set(config.get("unavailable_dates", [])):
        return []
    max_days = int(config.get("max_days_ahead") or 0)
    if max_days and target > date.today() + timedelta(days=max_days):
        return []
    hours = config["hours"].get(weekday_key(target))
    if not hours:
        return []

    start = datetime.combine(target, parse_clock(hours[0]))
    end = datetime.combine(target, parse_clock(hours[1]))
    interval = timedelta(minutes=int(config["slot_minutes"]))
    duration = timedelta(minutes=service_duration(service_name))
    min_start = datetime.now() + timedelta(hours=int(config.get("min_notice_hours") or 0))
    bookings = [
        row
        for row in list_bookings()
        if row.get("date") == target.isoformat()
        and row.get("status") != "cancelled"
        and row.get("id") != ignore_booking_id
    ]

    slots: list[str] = []
    cursor = start
    while cursor + duration <= end:
        value = cursor.strftime("%H:%M")
        slot_end = cursor + duration
        conflicts = False
        for row in bookings:
            booked_start = datetime.combine(target, parse_clock(row["time"]))
            booked_end = booked_start + timedelta(minutes=service_duration(row.get("service", "")))
            if intervals_overlap(cursor, slot_end, booked_start, booked_end):
                conflicts = True
                break
        if cursor >= min_start and not conflicts and not crosses_break(cursor, slot_end, target, config.get("breaks", [])):
            slots.append(value)
        cursor += interval
    return slots


def load_sessions() -> dict[str, Any]:
    ensure_data_files()
    try:
        return json.loads(SESSIONS_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_sessions(sessions: dict[str, Any]) -> None:
    ensure_data_files()
    SESSIONS_JSON.write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_phone(value: str) -> str:
    return re.sub(r"\D+", "", value)


def parse_user_date(text: str) -> date | None:
    cleaned = text.lower().strip()
    today = date.today()
    if cleaned in {"hoje"}:
        return today
    if cleaned in {"amanha", "amanhã"}:
        return today + timedelta(days=1)

    match = re.search(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", cleaned)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3)) if match.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None

    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", cleaned)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def parse_user_time(text: str) -> str | None:
    match = re.search(r"(\d{1,2})(?::|h)?(\d{2})?", text.lower())
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    if 0 <= hour <= 23 and minute in {0, 15, 30, 45}:
        return f"{hour:02d}:{minute:02d}"
    return None


def match_service(text: str) -> str | None:
    config = load_config()
    cleaned = text.lower().strip()
    if cleaned.isdigit():
        index = int(cleaned) - 1
        if 0 <= index < len(config["services"]):
            return config["services"][index]["name"]
    for service in config["services"]:
        service_name = service["name"]
        if cleaned in service_name.lower() or service_name.lower() in cleaned:
            return service_name
    return None


def service_menu() -> str:
    services = load_config()["services"]
    options = "\n".join(f"{i + 1}. {service['name']}" for i, service in enumerate(services))
    return f"Oi! Eu sou o assistente de agendamento. Qual servico voce quer marcar?\n{options}"


def maybe_polish_with_openai(reply: str, user_message: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or os.getenv("OPENAI_POLISH_WHATSAPP", "false").lower() != "true":
        return reply

    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "instructions": (
            "Voce reescreve mensagens curtas de WhatsApp em portugues do Brasil. "
            "Preserve exatamente datas, horarios, nomes de servicos e instrucoes. "
            "Nao invente disponibilidade nem acrescente promessas."
        ),
        "input": f"Mensagem do cliente: {user_message}\nResposta factual: {reply}",
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return reply

    chunks: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                chunks.append(content["text"])
    polished = "\n".join(chunks).strip()
    return polished or reply


def whatsapp_reply(phone: str, message: str) -> str:
    sessions = load_sessions()
    phone_key = normalize_phone(phone) or "unknown"
    session = sessions.get(phone_key, {"step": "service", "booking": {}})
    text = message.strip()

    if not text or text.lower() in {"menu", "inicio", "iniciar", "recomecar", "recomeçar"}:
        sessions[phone_key] = {"step": "service", "booking": {}}
        save_sessions(sessions)
        return service_menu()

    if text.lower() in {"cancelar", "sair"}:
        sessions.pop(phone_key, None)
        save_sessions(sessions)
        return "Tudo certo, cancelei esta conversa. Quando quiser marcar, me mande 'menu'."

    booking = session.setdefault("booking", {})
    step = session.get("step", "service")
    reply = ""

    if step == "service":
        service = match_service(text)
        if not service:
            reply = service_menu()
        else:
            booking["service"] = service
            session["step"] = "date"
            reply = f"Perfeito, {service}. Para qual data? Pode mandar no formato dd/mm ou escrever hoje/amanha."

    elif step == "date":
        target = parse_user_date(text)
        if not target or target < date.today():
            reply = "Nao consegui entender a data. Envie, por exemplo, 31/05 ou amanha."
        else:
            booking["date"] = target.isoformat()
            slots = available_slots(target, booking.get("service", ""))
            if not slots:
                reply = "Nao tenho horarios livres nessa data. Pode me mandar outra data?"
            else:
                session["step"] = "time"
                shown = ", ".join(slots[:10])
                reply = f"Horarios disponiveis em {target.strftime('%d/%m/%Y')}: {shown}. Qual prefere?"

    elif step == "time":
        chosen = parse_user_time(text)
        target = datetime.strptime(booking["date"], "%Y-%m-%d").date()
        slots = available_slots(target, booking.get("service", ""))
        if not chosen or chosen not in slots:
            shown = ", ".join(slots[:10]) or "nenhum horario livre"
            reply = f"Esse horario nao esta disponivel. Opcoes: {shown}."
        else:
            booking["time"] = chosen
            session["step"] = "name"
            reply = "Fechado. Qual nome devo colocar no agendamento?"

    elif step == "name":
        name = text.strip()
        if len(name) < 2:
            reply = "Me mande o nome completo ou apelido para identificar seu agendamento."
        else:
            booking["name"] = name
            booking["phone"] = phone_key
            booking["source"] = "whatsapp"
            valid, error = validate_booking(booking)
            if not valid:
                session["step"] = "date"
                booking.pop("date", None)
                booking.pop("time", None)
                reply = f"{error} Vamos tentar de novo. Para qual data?"
            else:
                saved = append_booking(booking)
                sessions.pop(phone_key, None)
                save_sessions(sessions)
                return maybe_polish_with_openai(
                    "Agendamento confirmado!\n"
                    f"Servico: {saved['service']}\n"
                    f"Data: {datetime.strptime(saved['date'], '%Y-%m-%d').strftime('%d/%m/%Y')}\n"
                    f"Horario: {saved['time']}\n"
                    f"Nome: {saved['name']}",
                    message,
                )

    sessions[phone_key] = session
    save_sessions(sessions)
    return maybe_polish_with_openai(reply, message)


def extract_whatsapp_message(content_type: str, payload: dict[str, Any]) -> tuple[str, str, str]:
    if "application/json" in content_type:
        # Meta WhatsApp Cloud API payload.
        try:
            change = payload["entry"][0]["changes"][0]["value"]
            message = change["messages"][0]
            phone = message.get("from", "")
            body = message.get("text", {}).get("body", "")
            return phone, body, "json"
        except (KeyError, IndexError, TypeError):
            return str(payload.get("from", "")), str(payload.get("message", "")), "json"

    phone = str(payload.get("From", payload.get("from", "")))
    body = str(payload.get("Body", payload.get("body", "")))
    return phone, body, "twilio"


def whatsapp_enabled() -> bool:
    twilio_ready = all(
        os.getenv(key)
        for key in ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM"]
    )
    meta_ready = all(
        os.getenv(key)
        for key in ["WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"]
    )
    return twilio_ready or meta_ready


def send_whatsapp_text(phone: str, text: str) -> tuple[bool, str]:
    clean_phone = normalize_phone(phone)
    if not clean_phone:
        return False, "Telefone invalido."

    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
    twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
    twilio_from = os.getenv("TWILIO_WHATSAPP_FROM")
    if twilio_sid and twilio_token and twilio_from:
        to = f"whatsapp:+{clean_phone}"
        body = urllib.parse.urlencode({"From": twilio_from, "To": to, "Body": text}).encode("utf-8")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
        request = urllib.request.Request(url, data=body, method="POST")
        token = base64.b64encode(f"{twilio_sid}:{twilio_token}".encode("utf-8")).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                return 200 <= response.status < 300, "Lembrete enviado via Twilio."
        except urllib.error.URLError as exc:
            return False, f"Falha ao enviar via Twilio: {exc}"

    meta_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    if meta_token and phone_number_id:
        payload = {
            "messaging_product": "whatsapp",
            "to": clean_phone,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        request = urllib.request.Request(
            f"https://graph.facebook.com/v20.0/{phone_number_id}/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {meta_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                return 200 <= response.status < 300, "Lembrete enviado via WhatsApp Cloud API."
        except urllib.error.URLError as exc:
            return False, f"Falha ao enviar via Cloud API: {exc}"

    return False, "Configure Twilio ou WhatsApp Cloud API para enviar mensagens."


def booking_reminder_text(booking: dict[str, str]) -> str:
    formatted_date = datetime.strptime(booking["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
    return (
        f"Lembrete do seu agendamento: {booking['service']} em {formatted_date} "
        f"as {booking['time']}. Se precisar remarcar, responda esta mensagem."
    )


def booking_matches_filters(row: dict[str, str], query: dict[str, list[str]]) -> bool:
    status = query.get("status", [""])[0]
    start = query.get("start", [""])[0]
    end = query.get("end", [""])[0]
    if status and row.get("status") != status:
        return False
    if start and row.get("date", "") < start:
        return False
    if end and row.get("date", "") > end:
        return False
    return True


class AppHandler(BaseHTTPRequestHandler):
    server_version = "AgendaBot/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/webhook/whatsapp":
            token = os.getenv("WHATSAPP_VERIFY_TOKEN", "agenda-bot")
            if query.get("hub.verify_token", [""])[0] == token:
                send_text(self, query.get("hub.challenge", [""])[0])
            else:
                send_text(self, "Token invalido", HTTPStatus.FORBIDDEN)
            return

        if path == "/api/config":
            send_json(self, public_config(load_config()) | {"whatsapp_enabled": whatsapp_enabled()})
            return

        if path == "/api/admin/session":
            send_json(self, {"authenticated": is_admin(self), "whatsapp_enabled": whatsapp_enabled()})
            return

        if path == "/api/bookings":
            if not require_admin(self):
                return
            rows = [row for row in list_bookings() if booking_matches_filters(row, query)]
            send_json(self, rows)
            return

        if path == "/api/slots":
            try:
                target = datetime.strptime(query.get("date", [""])[0], "%Y-%m-%d").date()
            except ValueError:
                send_json(self, {"error": "Informe date=YYYY-MM-DD"}, HTTPStatus.BAD_REQUEST)
                return
            service = query.get("service", [""])[0]
            ignore_id = query.get("ignore_id", [""])[0]
            send_json(self, {"date": target.isoformat(), "slots": available_slots(target, service, ignore_id)})
            return

        if path in {"/", "/index.html"}:
            self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return

        if path == "/admin":
            self.serve_file(STATIC_DIR / "admin.html", "text/html; charset=utf-8")
            return

        file_path = (STATIC_DIR / path.lstrip("/")).resolve()
        if STATIC_DIR.resolve() in file_path.parents and file_path.exists():
            mime = "text/css; charset=utf-8" if file_path.suffix == ".css" else "application/javascript; charset=utf-8"
            self.serve_file(file_path, mime)
            return

        send_json(self, {"error": "Nao encontrado"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_type = self.headers.get("Content-Type", "")

        try:
            payload = read_json_body(self) if "application/json" in content_type else read_form_body(self)
        except json.JSONDecodeError:
            send_json(self, {"error": "JSON invalido"}, HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/admin/login":
            if secrets.compare_digest(str(payload.get("password", "")), admin_password()):
                token = make_admin_session()
                send_json_with_headers(
                    self,
                    {"ok": True},
                    headers={
                        "Set-Cookie": (
                            f"admin_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=43200"
                        )
                    },
                )
            else:
                send_json(self, {"error": "Senha invalida."}, HTTPStatus.UNAUTHORIZED)
            return

        if path == "/api/admin/logout":
            send_json_with_headers(
                self,
                {"ok": True},
                headers={"Set-Cookie": "admin_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"},
            )
            return

        if path == "/api/bookings":
            valid, error = validate_booking(payload)
            if not valid:
                send_json(self, {"error": error}, HTTPStatus.BAD_REQUEST)
                return
            send_json(self, append_booking(payload), HTTPStatus.CREATED)
            return

        booking_action = re.fullmatch(r"/api/bookings/([^/]+)/(cancel|reschedule|reminder)", path)
        if booking_action:
            if not require_admin(self):
                return
            booking_id, action = booking_action.group(1), booking_action.group(2)
            booking = find_booking(booking_id)
            if not booking:
                send_json(self, {"error": "Agendamento nao encontrado."}, HTTPStatus.NOT_FOUND)
                return
            if action == "cancel":
                updated = update_booking(
                    booking_id,
                    {
                        "status": "cancelled",
                        "cancelled_at": datetime.now().isoformat(timespec="seconds"),
                    },
                )
                send_json(self, updated)
                return
            if action == "reschedule":
                candidate = booking | {
                    "date": str(payload.get("date", booking["date"])),
                    "time": str(payload.get("time", booking["time"])),
                    "id": booking_id,
                }
                valid, error = validate_booking(candidate)
                if not valid:
                    send_json(self, {"error": error}, HTTPStatus.BAD_REQUEST)
                    return
                updated = update_booking(
                    booking_id,
                    {"date": candidate["date"], "time": candidate["time"], "status": "confirmed"},
                )
                send_json(self, updated)
                return
            if action == "reminder":
                if booking.get("status") == "cancelled":
                    send_json(self, {"error": "Agendamento cancelado nao recebe lembrete."}, HTTPStatus.BAD_REQUEST)
                    return
                ok, info = send_whatsapp_text(booking["phone"], booking_reminder_text(booking))
                if not ok:
                    send_json(self, {"error": info}, HTTPStatus.BAD_REQUEST)
                    return
                updated = update_booking(
                    booking_id,
                    {"reminder_sent_at": datetime.now().isoformat(timespec="seconds")},
                )
                send_json(self, {"booking": updated, "message": info})
                return

        if path == "/webhook/whatsapp":
            phone, body, provider = extract_whatsapp_message(content_type, payload)
            reply = whatsapp_reply(phone, body)
            if provider == "twilio":
                twiml = f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Message>{html.escape(reply)}</Message></Response>"
                send_text(self, twiml, content_type="text/xml; charset=utf-8")
            else:
                send_json(self, {"reply": reply})
            return

        send_json(self, {"error": "Nao encontrado"}, HTTPStatus.NOT_FOUND)

    def serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            send_text(self, "Nao encontrado", HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    load_dotenv()
    ensure_data_files()
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), AppHandler)
    local_url = f"http://127.0.0.1:{port}"
    print(f"AgendaBot rodando em {local_url} (bind: {host}:{port})")
    print(f"Planilha CSV: {BOOKINGS_CSV}")
    server.serve_forever()


if __name__ == "__main__":
    main()
