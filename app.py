from __future__ import annotations

import csv
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
    "name",
    "phone",
    "service",
    "date",
    "time",
    "notes",
    "source",
    "status",
]

DEFAULT_CONFIG: dict[str, Any] = {
    "business_name": "Agenda Facil",
    "timezone": "America/Sao_Paulo",
    "slot_minutes": 30,
    "services": [
        "Consulta",
        "Retorno",
        "Avaliacao",
        "Atendimento online",
    ],
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
        return merged
    return DEFAULT_CONFIG


def ensure_data_files() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if not BOOKINGS_CSV.exists():
        with BOOKINGS_CSV.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()
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


def list_bookings() -> list[dict[str, str]]:
    ensure_data_files()
    with BOOKINGS_CSV.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_booking(payload: dict[str, str]) -> dict[str, str]:
    ensure_data_files()
    row = {
        "id": secrets.token_hex(6),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "name": payload.get("name", "").strip(),
        "phone": payload.get("phone", "").strip(),
        "service": payload.get("service", "").strip(),
        "date": payload.get("date", "").strip(),
        "time": payload.get("time", "").strip(),
        "notes": payload.get("notes", "").strip(),
        "source": payload.get("source", "site").strip(),
        "status": payload.get("status", "confirmed").strip(),
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
    slots = available_slots(target)
    if payload["time"] not in slots:
        return False, "Horario indisponivel."
    return True, ""


def available_slots(target: date) -> list[str]:
    config = load_config()
    hours = config["hours"].get(weekday_key(target))
    if not hours:
        return []

    start = datetime.combine(target, parse_clock(hours[0]))
    end = datetime.combine(target, parse_clock(hours[1]))
    interval = timedelta(minutes=int(config["slot_minutes"]))
    booked = {
        row["time"]
        for row in list_bookings()
        if row.get("date") == target.isoformat() and row.get("status") != "cancelled"
    }

    slots: list[str] = []
    now = datetime.now()
    cursor = start
    while cursor + interval <= end:
        value = cursor.strftime("%H:%M")
        if value not in booked and cursor > now:
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
            return config["services"][index]
    for service in config["services"]:
        if cleaned in service.lower() or service.lower() in cleaned:
            return service
    return None


def service_menu() -> str:
    services = load_config()["services"]
    options = "\n".join(f"{i + 1}. {name}" for i, name in enumerate(services))
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
            slots = available_slots(target)
            if not slots:
                reply = "Nao tenho horarios livres nessa data. Pode me mandar outra data?"
            else:
                session["step"] = "time"
                shown = ", ".join(slots[:10])
                reply = f"Horarios disponiveis em {target.strftime('%d/%m/%Y')}: {shown}. Qual prefere?"

    elif step == "time":
        chosen = parse_user_time(text)
        target = datetime.strptime(booking["date"], "%Y-%m-%d").date()
        slots = available_slots(target)
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
            send_json(self, load_config())
            return

        if path == "/api/bookings":
            send_json(self, list_bookings())
            return

        if path == "/api/slots":
            try:
                target = datetime.strptime(query.get("date", [""])[0], "%Y-%m-%d").date()
            except ValueError:
                send_json(self, {"error": "Informe date=YYYY-MM-DD"}, HTTPStatus.BAD_REQUEST)
                return
            send_json(self, {"date": target.isoformat(), "slots": available_slots(target)})
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

        if path == "/api/bookings":
            valid, error = validate_booking(payload)
            if not valid:
                send_json(self, {"error": error}, HTTPStatus.BAD_REQUEST)
                return
            send_json(self, append_booking(payload), HTTPStatus.CREATED)
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
