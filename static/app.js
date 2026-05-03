const state = {
  config: null,
  selectedSlot: "",
  bookings: [],
  machines: [],
};

const $ = (id) => document.getElementById(id);

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function notice(id, message, type = "") {
  const el = $(id);
  if (!el) return;
  el.textContent = message;
  el.className = `notice ${type}`.trim();
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}

function today() {
  return state.config?.today || new Date().toISOString().slice(0, 10);
}

function connectSocket() {
  if (!window.io) return;
  const socket = io({ transports: ["polling"], upgrade: false });
  socket.on("booking_created", refreshCurrentPage);
  socket.on("booking_updated", refreshCurrentPage);
  socket.on("bookings_changed", refreshCurrentPage);
  socket.on("machine_updated", refreshCurrentPage);
  if (document.body.dataset.page === "reception") {
    socket.emit("join_reception");
  }
}

async function loadConfig() {
  state.config = await api("/api/config");
}

function facilityOptions() {
  return Object.entries(state.config.facilities)
    .map(([value, item]) => `<option value="${value}">${item.label}</option>`)
    .join("");
}

function selectedFacility() {
  return $("facilitySelect")?.value || "in-house";
}

function selectedRoom() {
  return $("roomSelect")?.value || "";
}

function renderRoomOptions() {
  const select = $("roomSelect");
  if (!select) return;
  const facility = state.config.facilities[selectedFacility()];
  select.innerHTML = facility.rooms.map((room) => `<option value="${room}">${room}</option>`).join("");
}

async function renderSlots() {
  const grid = $("slotGrid");
  if (!grid) return;
  const facilityKey = selectedFacility();
  const room = selectedRoom();
  const facility = state.config.facilities[facilityKey];
  let availability = { taken: [], machine: { working: true, note: "" } };

  if (room) {
    try {
      availability = await api(`/api/availability?facility=${encodeURIComponent(facilityKey)}&room=${encodeURIComponent(room)}&date=${today()}`);
    } catch (error) {
      notice("bookingNotice", error.message, "error");
    }
  }

  grid.innerHTML = facility.slots.map((slot) => {
    const taken = availability.taken.includes(slot);
    const closed = !availability.machine.working;
    const active = state.selectedSlot === slot;
    const disabledClass = taken ? "taken" : closed ? "closed" : "";
    const label = taken ? `${slot} taken` : closed ? `${slot} offline` : slot;
    return `<button class="slot ${active ? "active" : ""} ${disabledClass}" data-slot="${slot}" ${taken || closed ? "disabled" : ""}>${label}</button>`;
  }).join("");

  grid.querySelectorAll(".slot:not(:disabled)").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedSlot = button.dataset.slot;
      renderSlots();
    });
  });
}

async function loadMachines() {
  state.machines = await api("/api/machines");
}

function machineLabel(machine) {
  const facility = state.config.facilities[machine.facility]?.label || machine.facility;
  return `${facility} - ${machine.room}`;
}

function renderStudentMachines() {
  const list = $("studentMachines");
  if (!list) return;
  list.innerHTML = state.machines.map((machine) => `
    <div class="machine">
      <div>
        <strong>${machineLabel(machine)}</strong>
        <div class="muted">${machine.note || "Ready"}</div>
      </div>
      <span class="pill ${machine.working ? "" : "off"}">${machine.working ? "Online" : "Offline"}</span>
    </div>
  `).join("");
}

async function submitBooking() {
  const button = $("bookButton");
  button.disabled = true;
  notice("bookingNotice", "Checking availability...");
  try {
    const data = await api("/api/book", {
      method: "POST",
      body: JSON.stringify({
        facility: selectedFacility(),
        room: selectedRoom(),
        slot: state.selectedSlot,
        date: today(),
        name: $("nameInput").value.trim(),
        email: $("emailInput").value.trim(),
        roomNumber: $("roomNumberInput").value.trim(),
      }),
    });
    notice("bookingNotice", data.message, "ok");
    state.selectedSlot = "";
    await refreshStudent();
  } catch (error) {
    notice("bookingNotice", error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function loadMyBookings() {
  const email = $("myBookingsEmail").value.trim();
  const list = $("myBookingsList");
  list.innerHTML = "<div class='muted'>Loading...</div>";
  try {
    const rows = await api(`/api/my-bookings?email=${encodeURIComponent(email)}`);
    list.innerHTML = rows.length ? rows.map(renderBookingCard).join("") : "<div class='muted'>No bookings found.</div>";
  } catch (error) {
    list.innerHTML = `<div class="notice error">${error.message}</div>`;
  }
}

function statusPill(booking) {
  const late = booking.late_return ? " late" : "";
  return `<span class="pill ${booking.status}${late}">${booking.status.replace("_", " ")}</span>`;
}

function renderBookingCard(booking) {
  return `
    <div class="booking">
      <div>
        <strong>${booking.name}</strong>
        <div class="muted">${booking.facility === "in-house" ? "In-House Laundry" : "Basement Laundry"} - ${booking.room}</div>
        <div>${booking.date} ${booking.slot}</div>
        <div class="muted">${booking.email} - Room ${booking.room_number}</div>
        ${booking.late_return ? `<div class="muted">Late by ${booking.late_minutes} minutes</div>` : ""}
      </div>
      <div>${statusPill(booking)}</div>
    </div>
  `;
}

async function refreshStudent() {
  await loadMachines();
  renderStudentMachines();
  await renderSlots();
}

async function initStudent() {
  await loadConfig();
  $("facilitySelect").innerHTML = facilityOptions();
  renderRoomOptions();
  $("facilitySelect").addEventListener("change", () => {
    state.selectedSlot = "";
    renderRoomOptions();
    renderSlots();
  });
  $("roomSelect").addEventListener("change", () => {
    state.selectedSlot = "";
    renderSlots();
  });
  $("bookButton").addEventListener("click", submitBooking);
  $("openMyBookings").addEventListener("click", () => $("myBookingsModal").showModal());
  $("closeMyBookings").addEventListener("click", () => $("myBookingsModal").close());
  $("loadMyBookings").addEventListener("click", loadMyBookings);
  await refreshStudent();
  connectSocket();
}

function renderStats(stats) {
  setText("statTotal", stats.total || 0);
  setText("statConfirmed", stats.confirmed || 0);
  setText("statCheckedIn", stats.checked_in || 0);
  setText("statCompleted", stats.completed || 0);
  setText("statAbandoned", stats.abandoned || 0);
  setText("statLate", stats.late || 0);
}

function visibleBookings() {
  const search = ($("bookingSearch")?.value || "").toLowerCase();
  if (!search) return state.bookings;
  return state.bookings.filter((booking) =>
    [booking.name, booking.email, booking.room_number, booking.room, booking.slot, booking.status]
      .join(" ")
      .toLowerCase()
      .includes(search)
  );
}

function renderReceptionBookings() {
  const list = $("receptionBookings");
  if (!list) return;
  const rows = visibleBookings();
  list.innerHTML = rows.length ? rows.map((booking) => `
    <div class="booking">
      <div>
        <strong>${booking.name}</strong>
        <div class="muted">${booking.email} - Residence room ${booking.room_number}</div>
        <div>${booking.facility === "in-house" ? "In-House Laundry" : "Basement Laundry"} - ${booking.room} - ${booking.slot}</div>
        ${booking.late_return ? `<div class="muted">Late return: ${booking.late_minutes} minutes</div>` : ""}
      </div>
      <div class="actions">
        ${statusPill(booking)}
        <button class="secondary" data-action="checkin" data-id="${booking.id}" ${booking.status !== "confirmed" ? "disabled" : ""}>Check In</button>
        <button class="primary" data-action="checkout" data-id="${booking.id}" ${booking.status !== "checked_in" ? "disabled" : ""}>Check Out</button>
      </div>
    </div>
  `).join("") : "<div class='muted'>No bookings for today.</div>";

  list.querySelectorAll("button[data-action]").forEach((button) => {
    button.addEventListener("click", () => updateBooking(button.dataset.action, button.dataset.id));
  });
}

async function updateBooking(action, id) {
  await api(`/api/${action}/${id}`, { method: "POST" });
  await refreshReception();
}

function renderReceptionMachines() {
  const list = $("receptionMachines");
  if (!list) return;
  list.innerHTML = state.machines.map((machine) => `
    <div class="machine">
      <div class="machine-top">
        <strong>${machineLabel(machine)}</strong>
        <span class="pill ${machine.working ? "" : "off"}">${machine.working ? "Online" : "Offline"}</span>
      </div>
      <textarea data-note="${machine.id}" placeholder="Machine note">${machine.note || ""}</textarea>
      <div class="actions">
        <button class="${machine.working ? "danger" : "primary"}" data-machine="${machine.id}" data-working="${machine.working ? "false" : "true"}">
          ${machine.working ? "Mark Offline" : "Mark Online"}
        </button>
      </div>
    </div>
  `).join("");

  list.querySelectorAll("button[data-machine]").forEach((button) => {
    button.addEventListener("click", async () => {
      const note = document.querySelector(`[data-note="${button.dataset.machine}"]`).value;
      await api(`/api/machines/${button.dataset.machine}`, {
        method: "PATCH",
        body: JSON.stringify({ working: button.dataset.working === "true", note }),
      });
      await refreshReception();
    });
  });
}

async function refreshReception() {
  const [bookings, machines, stats] = await Promise.all([
    api(`/api/bookings?date=${today()}`),
    api("/api/machines"),
    api("/api/stats"),
  ]);
  state.bookings = bookings;
  state.machines = machines;
  renderStats(stats);
  renderReceptionBookings();
  renderReceptionMachines();
}

async function initReception() {
  await loadConfig();
  $("refreshReception").addEventListener("click", refreshReception);
  $("bookingSearch").addEventListener("input", renderReceptionBookings);
  await refreshReception();
  connectSocket();
}

function refreshCurrentPage() {
  if (document.body.dataset.page === "student") refreshStudent();
  if (document.body.dataset.page === "reception") refreshReception();
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.body.dataset.page === "student") initStudent();
  if (document.body.dataset.page === "reception") initReception();
});
