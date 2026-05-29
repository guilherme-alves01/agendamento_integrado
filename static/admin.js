const rows = document.querySelector("#bookingRows");
const count = document.querySelector("#bookingCount");
const emptyState = document.querySelector("#emptyState");
const refreshButton = document.querySelector("#refreshButton");
const loginPanel = document.querySelector("#loginPanel");
const adminApp = document.querySelector("#adminApp");
const loginForm = document.querySelector("#loginForm");
const loginMessage = document.querySelector("#loginMessage");
const logoutButton = document.querySelector("#logoutButton");
const statusFilter = document.querySelector("#statusFilter");
const startFilter = document.querySelector("#startFilter");
const endFilter = document.querySelector("#endFilter");

let whatsappEnabled = false;

function formatDate(value) {
  if (!value) return "";
  const [year, month, day] = value.split("-");
  return `${day}/${month}/${year}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setLoginMessage(text, type = "") {
  loginMessage.textContent = text;
  loginMessage.className = `message ${type}`;
}

function setMode(authenticated) {
  loginPanel.hidden = authenticated;
  adminApp.hidden = !authenticated;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "Nao foi possivel concluir a acao.");
  }
  return data;
}

function queryString() {
  const params = new URLSearchParams();
  if (statusFilter.value) params.set("status", statusFilter.value);
  if (startFilter.value) params.set("start", startFilter.value);
  if (endFilter.value) params.set("end", endFilter.value);
  return params.toString();
}

function actionButtons(booking) {
  const disabled = booking.status === "cancelled";
  const reminderLabel = booking.reminder_sent_at ? "Reenviar lembrete" : "Lembrete";
  return `
    <div class="row-actions">
      <button class="secondary-button compact" data-action="reschedule" data-id="${booking.id}" ${disabled ? "disabled" : ""}>Remarcar</button>
      <button class="secondary-button compact" data-action="reminder" data-id="${booking.id}" ${disabled || !whatsappEnabled ? "disabled" : ""}>${reminderLabel}</button>
      <button class="danger-button compact" data-action="cancel" data-id="${booking.id}" ${disabled ? "disabled" : ""}>Cancelar</button>
    </div>
  `;
}

async function loadBookings() {
  rows.innerHTML = "";
  emptyState.hidden = true;
  const suffix = queryString();
  const bookings = await api(`/api/bookings${suffix ? `?${suffix}` : ""}`);
  const sorted = bookings.sort((a, b) => `${a.date} ${a.time}`.localeCompare(`${b.date} ${b.time}`));

  count.textContent = `${sorted.length} ${sorted.length === 1 ? "registro" : "registros"}`;
  emptyState.hidden = sorted.length !== 0;
  rows.innerHTML = sorted
    .map(
      (booking) => `
        <tr class="${booking.status === "cancelled" ? "is-cancelled" : ""}">
          <td>${formatDate(escapeHtml(booking.date))}</td>
          <td>${escapeHtml(booking.time)}</td>
          <td>${escapeHtml(booking.name)}</td>
          <td>${escapeHtml(booking.phone)}</td>
          <td>${escapeHtml(booking.service)}</td>
          <td>${escapeHtml(booking.source)}</td>
          <td><span class="status-pill">${escapeHtml(booking.status)}</span></td>
          <td>${actionButtons(booking)}</td>
        </tr>
      `,
    )
    .join("");
}

async function checkSession() {
  const session = await api("/api/admin/session");
  whatsappEnabled = Boolean(session.whatsapp_enabled);
  setMode(session.authenticated);
  if (session.authenticated) {
    await loadBookings();
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setLoginMessage("");
  const password = new FormData(loginForm).get("password");
  try {
    await api("/api/admin/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    loginForm.reset();
    await checkSession();
  } catch (error) {
    setLoginMessage(error.message, "error");
  }
});

logoutButton.addEventListener("click", async () => {
  await api("/api/admin/logout", { method: "POST", body: "{}" });
  setMode(false);
});

refreshButton.addEventListener("click", loadBookings);
statusFilter.addEventListener("change", loadBookings);
startFilter.addEventListener("change", loadBookings);
endFilter.addEventListener("change", loadBookings);

rows.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;

  const { action, id } = button.dataset;
  try {
    if (action === "cancel") {
      if (!confirm("Cancelar este agendamento?")) return;
      await api(`/api/bookings/${id}/cancel`, { method: "POST", body: "{}" });
    }
    if (action === "reschedule") {
      const date = prompt("Nova data no formato YYYY-MM-DD:");
      if (!date) return;
      const time = prompt("Novo horario no formato HH:MM:");
      if (!time) return;
      await api(`/api/bookings/${id}/reschedule`, {
        method: "POST",
        body: JSON.stringify({ date, time }),
      });
    }
    if (action === "reminder") {
      await api(`/api/bookings/${id}/reminder`, { method: "POST", body: "{}" });
      alert("Lembrete enviado.");
    }
    await loadBookings();
  } catch (error) {
    alert(error.message);
  }
});

checkSession().catch((error) => {
  setMode(false);
  setLoginMessage(error.message, "error");
});
