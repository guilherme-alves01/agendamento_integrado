const rows = document.querySelector("#bookingRows");
const count = document.querySelector("#bookingCount");
const emptyState = document.querySelector("#emptyState");
const refreshButton = document.querySelector("#refreshButton");

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

async function loadBookings() {
  rows.innerHTML = "";
  emptyState.hidden = true;
  const response = await fetch("/api/bookings");
  const bookings = await response.json();
  const sorted = bookings.sort((a, b) => `${a.date} ${a.time}`.localeCompare(`${b.date} ${b.time}`));

  count.textContent = `${sorted.length} ${sorted.length === 1 ? "registro" : "registros"}`;
  emptyState.hidden = sorted.length !== 0;
  rows.innerHTML = sorted
    .map(
      (booking) => `
        <tr>
          <td>${formatDate(escapeHtml(booking.date))}</td>
          <td>${escapeHtml(booking.time)}</td>
          <td>${escapeHtml(booking.name)}</td>
          <td>${escapeHtml(booking.phone)}</td>
          <td>${escapeHtml(booking.service)}</td>
          <td>${escapeHtml(booking.source)}</td>
          <td>${escapeHtml(booking.status)}</td>
        </tr>
      `,
    )
    .join("");
}

refreshButton.addEventListener("click", loadBookings);
loadBookings();
