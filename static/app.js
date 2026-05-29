const form = document.querySelector("#bookingForm");
const serviceSelect = document.querySelector("#serviceSelect");
const dateInput = document.querySelector("#dateInput");
const slots = document.querySelector("#slots");
const slotHelp = document.querySelector("#slotHelp");
const message = document.querySelector("#message");

let selectedTime = "";

function todayISO() {
  const now = new Date();
  const offset = now.getTimezoneOffset();
  return new Date(now.getTime() - offset * 60_000).toISOString().slice(0, 10);
}

function setTodayChip() {
  const now = new Date();
  const months = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"];
  document.querySelector("#todayDay").textContent = String(now.getDate()).padStart(2, "0");
  document.querySelector("#todayMonth").textContent = months[now.getMonth()];
}

function setMessage(text, type = "") {
  message.textContent = text;
  message.className = `message ${type}`;
}

async function loadConfig() {
  const response = await fetch("/api/config");
  const config = await response.json();
  document.querySelector("#businessName").textContent = config.business_name;
  document.querySelector("#slotSize").textContent = `${config.slot_minutes} min`;
  serviceSelect.innerHTML = config.services
    .map((service) => `<option value="${service}">${service}</option>`)
    .join("");
}

async function loadSlots() {
  selectedTime = "";
  slots.innerHTML = "";
  const day = dateInput.value;
  if (!day) {
    slotHelp.textContent = "Escolha uma data para carregar os horarios.";
    return;
  }

  slotHelp.textContent = "Carregando horarios...";
  const response = await fetch(`/api/slots?date=${encodeURIComponent(day)}`);
  const data = await response.json();

  if (!response.ok) {
    slotHelp.textContent = data.error || "Nao foi possivel carregar os horarios.";
    return;
  }

  if (!data.slots.length) {
    slotHelp.textContent = "Nao ha horarios livres nessa data.";
    return;
  }

  slotHelp.textContent = "Selecione um horario livre.";
  slots.innerHTML = data.slots
    .map(
      (slot) => `
        <label class="slot-option">
          <input type="radio" name="time" value="${slot}" />
          <span>${slot}</span>
        </label>
      `,
    )
    .join("");
}

dateInput.addEventListener("change", loadSlots);
slots.addEventListener("change", (event) => {
  if (event.target.name === "time") {
    selectedTime = event.target.value;
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage("");

  if (!selectedTime) {
    setMessage("Escolha um horario disponivel.", "error");
    return;
  }

  const data = Object.fromEntries(new FormData(form).entries());
  data.time = selectedTime;
  data.source = "site";

  const response = await fetch("/api/bookings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  const result = await response.json();

  if (!response.ok) {
    setMessage(result.error || "Nao foi possivel confirmar.", "error");
    await loadSlots();
    return;
  }

  form.reset();
  dateInput.value = todayISO();
  setMessage(`Agendamento confirmado para ${result.date} as ${result.time}.`, "success");
  await loadSlots();
});

setTodayChip();
dateInput.min = todayISO();
dateInput.value = todayISO();
loadConfig().then(loadSlots).catch(() => {
  setMessage("Nao foi possivel carregar a agenda.", "error");
});
