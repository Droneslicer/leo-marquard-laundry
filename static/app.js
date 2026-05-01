// ===================== STATE =====================
let currentFacility    = null;
let currentRoom        = null;
let currentDate        = new Date().toISOString().slice(0, 10);
let selectedSlotLabel  = null;
let isLoadingSlots     = false;
let isBooking          = false;
let machineWorking     = true;
let machineNote        = "";

// 🟢 WebSocket state
let socket = null;
let wsConnected = false;

// ===================== DOM =====================
const facilityTrigger      = document.getElementById('facilityTrigger');
const roomTrigger          = document.getElementById('roomTrigger');
const facilitySelectedSpan = document.getElementById('facilitySelectedText');
const roomSelectedSpan     = document.getElementById('roomSelectedText');
const slotsContainer       = document.getElementById('slotsContainer');
const dateInput            = document.getElementById('slotDate');
const fullNameInput        = document.getElementById('fullName');
const emailInput           = document.getElementById('email');
const roomNumberInput      = document.getElementById('roomNumber');
const globalOverlay        = document.getElementById('globalOverlay');
const bookButton           = document.getElementById('bookButton');
const selectionPreview     = document.getElementById('selectionPreview');

// ===================== CONFIG =====================
const FACILITY_CONFIG = {
  "in-house": {
    display: "🏠 In-House Laundry",
    rooms: ["2nd Floor", "3rd Floor", "5th Floor", "7th Floor", "9th Floor"],
    getTimeSlots: () => ["08:00 - 11:00","11:00 - 14:00","14:00 - 17:00","17:00 - 20:00","20:00 - 23:00"]
  },
  "basement": {
    display: "🏚️ Basement Laundry",
    rooms: ["Basement 1", "Basement 3"],
    getTimeSlots: () => ["08:00 - 10:00","10:00 - 12:00","12:00 - 14:00","14:00 - 16:00","16:00 - 18:00","18:00 - 20:00","20:00 - 22:00"]
  }
};

// ===================== DATE LIMITS =====================
const today   = new Date().toISOString().slice(0, 10);
dateInput.value = today;
dateInput.min   = today;
dateInput.max   = today;   // today only
dateInput.disabled = true; // no need to change date
currentDate     = today;

// ===================== WEBSOCKET INITIALIZATION =====================
function initWebSocket() {
    socket = io({
        transports: ['websocket', 'polling'],
        reconnection: true,
        reconnectionAttempts: 5,
        reconnectionDelay: 1000
    });

    socket.on('connect', () => {
        console.log('✅ WebSocket connected');
        wsConnected = true;
        showToast('Live updates connected', 'success');
        updateWSStatus(true);
    });

    socket.on('disconnect', () => {
        console.log('❌ WebSocket disconnected');
        wsConnected = false;
        updateWSStatus(false);
    });

    socket.on('reconnect', () => {
        console.log('🔄 WebSocket reconnected');
        wsConnected = true;
        showToast('Reconnected to live updates', 'success');
        updateWSStatus(true);
        // Refresh current view on reconnect
        if (currentFacility && currentRoom) renderSlots();
    });

    socket.on('connect_error', (error) => {
        console.error('WebSocket connection error:', error);
        wsConnected = false;
        updateWSStatus(false);
    });

    // 🟢 Listen for slot availability changes
    socket.on('slots_updated', (data) => {
        console.log('Slots updated:', data);
        if (data.facility === currentFacility &&
            data.room === currentRoom &&
            data.date === currentDate) {
            // Refresh slots if it affects current view
            renderSlots();
            showToast('Slot availability updated', 'info');
        }
    });

    // 🟢 Listen for booking conflicts
    socket.on('booking_conflict', (data) => {
        if (data.slot === selectedSlotLabel &&
            data.facility === currentFacility &&
            data.room === currentRoom) {
            showToast('⚠️ This slot was just booked by someone else', 'error');
            selectedSlotLabel = null;
            updatePreview();
            updateBookButton();
            renderSlots();
        }
    });

    // 🟢 Listen for machine status changes
    socket.on('machine_updated', (data) => {
        if (data.machine && data.machine.facility === currentFacility &&
            data.machine.room === currentRoom) {
            machineWorking = data.machine.working;
            machineNote = data.machine.note || "";
            renderSlots();
            if (!data.machine.working) {
                showToast(`⚠️ Machine is now out of service: ${machineNote}`, 'warning');
            } else {
                showToast(`✅ Machine is now available`, 'success');
            }
        }
    });
}

// Update connection status in UI
function updateWSStatus(connected) {
    const statusEl = document.getElementById('wsStatus');
    if (statusEl) {
        if (connected) {
            statusEl.innerHTML = '🟢 Live Updates';
            statusEl.style.background = 'rgba(0, 229, 176, 0.15)';
            statusEl.style.color = '#00e5b0';
        } else {
            statusEl.innerHTML = '🔴 Reconnecting...';
            statusEl.style.background = 'rgba(255, 94, 108, 0.15)';
            statusEl.style.color = '#ff5e6c';
        }
    }
}

// Add WebSocket status indicator to page
function addWSStatusIndicator() {
    const indicator = document.createElement('div');
    indicator.id = 'wsStatus';
    indicator.style.cssText = `
        position: fixed;
        bottom: 20px;
        left: 20px;
        padding: 6px 12px;
        border-radius: 20px;
        font-family: 'DM Mono', monospace;
        font-size: 0.7rem;
        background: rgba(0, 0, 0, 0.7);
        backdrop-filter: blur(8px);
        z-index: 9999;
        pointer-events: none;
        transition: all 0.3s ease;
    `;
    indicator.innerHTML = '🔌 Connecting...';
    document.body.appendChild(indicator);
}

// Show booking window status
function getBookingWindowStatus(facility) {
  const openHour = 6; // both facilities open at 06:00
  const now      = new Date();
  if (now.getHours() < openHour) {
    return { open: false, msg: `Bookings open at 06:00` };
  }
  return { open: true, msg: "" };
}

// ===================== TOAST =====================
let toastTimer = null;
function showToast(text, type = "success") {
  const toast = document.getElementById("toast");
  toast.textContent = text;
  toast.className = `show ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.className = type; }, 3400);
}

// ===================== BOOK BUTTON =====================
function updateBookButton() {
  const ready = currentFacility && currentRoom && selectedSlotLabel && machineWorking;
  bookButton.disabled = !ready || isBooking;
}

// ===================== PREVIEW =====================
function updatePreview() {
  if (!currentFacility && !currentRoom && !selectedSlotLabel) {
    selectionPreview.className = "selected-preview";
    selectionPreview.innerHTML = `<div style="text-align:center;color:var(--text-muted)">— no selection yet —</div>`;
    return;
  }
  selectionPreview.className = "selected-preview has-data";
  const rows = [];
  if (currentFacility) rows.push(`<div class="preview-row"><span>FACILITY</span><span class="preview-val">${FACILITY_CONFIG[currentFacility].display}</span></div>`);
  if (currentRoom)     rows.push(`<div class="preview-row"><span>ROOM</span><span class="preview-val">${currentRoom}</span></div>`);
  if (currentDate)     rows.push(`<div class="preview-row"><span>DATE</span><span class="preview-val">${currentDate}</span></div>`);
  if (selectedSlotLabel) rows.push(`<div class="preview-row"><span>SLOT</span><span class="preview-val">${selectedSlotLabel}</span></div>`);
  selectionPreview.innerHTML = rows.join('');
}

// ===================== OVERLAY / DROPDOWNS =====================
function closeDropdowns() {
  const menu = document.querySelector('.dropdown-menu');
  if (menu) menu.remove();
  globalOverlay.classList.remove('active');
  facilityTrigger.classList.remove('open');
  roomTrigger.classList.remove('open');
}

globalOverlay.addEventListener("click", () => {
  closeDropdowns();
  closeMyBookingsModal();
  globalOverlay.classList.remove("active");
});

function openDropdown(type) {
  closeDropdowns();
  const trigger = type === "facility" ? facilityTrigger : roomTrigger;
  if (type === "room" && !currentFacility) return;
  trigger.classList.add('open');

  const menu = document.createElement("div");
  menu.className = "dropdown-menu";
  menu.style.top       = "50%";
  menu.style.left      = "50%";
  menu.style.transform = "translate(-50%, -50%)";
  menu.style.width     = "min(340px, 90vw)";

  if (type === "facility") {
    Object.keys(FACILITY_CONFIG).forEach(key => {
      const opt = document.createElement("div");
      opt.className   = "dropdown-option";
      opt.textContent = FACILITY_CONFIG[key].display;
      opt.onclick = () => { selectFacility(key); closeDropdowns(); };
      menu.appendChild(opt);
    });
  } else {
    FACILITY_CONFIG[currentFacility].rooms.forEach(room => {
      const opt = document.createElement("div");
      opt.className   = "dropdown-option";
      opt.textContent = room;
      opt.onclick = () => { selectRoom(room); closeDropdowns(); };
      menu.appendChild(opt);
    });
  }

  document.body.appendChild(menu);
  globalOverlay.classList.add("active");
}

facilityTrigger.onclick = (e) => { e.stopPropagation(); openDropdown("facility"); };
roomTrigger.onclick     = (e) => { e.stopPropagation(); if (!roomTrigger.hasAttribute('data-locked')) openDropdown("room"); };

// ===================== SELECT =====================
function selectFacility(key) {
  currentFacility   = key;
  currentRoom       = null;
  selectedSlotLabel = null;
  machineWorking    = true;
  machineNote       = "";
  facilitySelectedSpan.textContent = FACILITY_CONFIG[key].display;
  roomSelectedSpan.textContent     = "Select room";
  roomTrigger.removeAttribute('data-locked');
  updatePreview();
  updateBookButton();
  renderSlots();
}

function selectRoom(roomName) {
  currentRoom       = roomName;
  selectedSlotLabel = null;
  roomSelectedSpan.textContent = roomName;
  updatePreview();
  updateBookButton();
  renderSlots();
}

function selectSlot(slot) {
  selectedSlotLabel = slot;
  document.querySelectorAll('.slot-node').forEach(node => {
    node.classList.toggle("selected-slot", node.dataset.slot === slot);
  });
  updatePreview();
  updateBookButton();
}

// ===================== DATE =====================
dateInput.addEventListener("change", (e) => {
  currentDate       = e.target.value;
  selectedSlotLabel = null;
  updatePreview();
  updateBookButton();
  renderSlots();
});

// ===================== API =====================
async function fetchAvailability() {
  const res  = await fetch(`/api/availability?facility=${encodeURIComponent(currentFacility)}&room=${encodeURIComponent(currentRoom)}&date=${currentDate}`);
  if (!res.ok) throw new Error("Network error");
  return await res.json();
}

// ===================== RENDER SLOTS =====================
function renderSkeletons() {
  slotsContainer.innerHTML = "";
  const count = currentFacility ? FACILITY_CONFIG[currentFacility].getTimeSlots().length : 5;
  for (let i = 0; i < count; i++) {
    const sk = document.createElement("div");
    sk.className = "slot-skeleton";
    slotsContainer.appendChild(sk);
  }
}

async function renderSlots() {
  if (!currentFacility || !currentRoom) {
    slotsContainer.innerHTML = `<div class="empty-state">Select facility &amp; room to view slots</div>`;
    removeMachineBanner();
    return;
  }

  // Check if booking window is open
  const window = getBookingWindowStatus(currentFacility);
  if (!window.open) {
    slotsContainer.innerHTML = `<div class="empty-state">🔒 ${window.msg}</div>`;
    removeMachineBanner();
    return;
  }

  if (isLoadingSlots) return;
  isLoadingSlots = true;
  renderSkeletons();

  try {
    const data      = await fetchAvailability();
    const takenSlots = data.taken || [];
    machineWorking  = data.machine_working !== false;
    machineNote     = data.machine_note || "";

    // machine down banner
    if (!machineWorking) {
      showMachineBanner(machineNote);
    } else {
      removeMachineBanner();
    }

    const slots = FACILITY_CONFIG[currentFacility].getTimeSlots();

    // If our selected slot got taken by someone else, clear it
    if (selectedSlotLabel && takenSlots.includes(selectedSlotLabel)) {
      selectedSlotLabel = null;
      updatePreview();
      updateBookButton();
      showToast("Your selected slot was just taken — please choose another", "error");

      // Emit slot conflict to server for logging
      if (socket && wsConnected) {
        socket.emit('slot_conflict', {
          facility: currentFacility,
          room: currentRoom,
          slot: selectedSlotLabel,
          date: currentDate
        });
      }
    }

    slotsContainer.innerHTML = "";

    slots.forEach((slot, i) => {
      const isTaken    = takenSlots.includes(slot) || !machineWorking;
      const isSelected = !isTaken && selectedSlotLabel === slot;
      const div        = document.createElement("div");

      div.className      = `slot-node ${isTaken ? "unavailable" : "available"}${isSelected ? " selected-slot" : ""}`;
      div.dataset.slot   = slot;
      div.style.animationDelay = `${i * 40}ms`;
      div.textContent    = slot;

      if (!isTaken) div.onclick = () => selectSlot(slot);
      slotsContainer.appendChild(div);
    });

    updateBookButton();
  } catch (err) {
    slotsContainer.innerHTML = `<div class="empty-state" style="color:#ff6b6b">⚠ Failed to load slots</div>`;
  } finally {
    isLoadingSlots = false;
  }
}

function showMachineBanner(note) {
  removeMachineBanner();
  const banner = document.createElement("div");
  banner.id        = "machineBanner";
  banner.className = "machine-banner";
  banner.innerHTML = `⚠ Machine out of service${note ? `: ${note}` : ""}`;
  slotsContainer.parentElement.insertBefore(banner, slotsContainer);
}

function removeMachineBanner() {
  const b = document.getElementById("machineBanner");
  if (b) b.remove();
}

// ===================== VALIDATION =====================
function validateInputs() {
  let ok = true;
  const name  = fullNameInput.value.trim();
  const email = emailInput.value.trim();
  const room  = roomNumberInput.value.trim();

  if (!name)  { fullNameInput.classList.add("invalid");   ok = false; }
  else          fullNameInput.classList.remove("invalid");

  if (!email.endsWith("@myuct.ac.za")) { emailInput.classList.add("invalid");  ok = false; }
  else                                    emailInput.classList.remove("invalid");

  if (!room)  { roomNumberInput.classList.add("invalid"); ok = false; }
  else          roomNumberInput.classList.remove("invalid");

  return ok;
}

emailInput.addEventListener("input", () => {
  if (emailInput.value.endsWith("@myuct.ac.za")) emailInput.classList.remove("invalid");
});

// ===================== BOOKING FLOW =====================
function buildPayload() {
  return {
    name:       fullNameInput.value.trim(),
    email:      emailInput.value.trim().toLowerCase(),
    roomNumber: roomNumberInput.value.trim(),
    facility:   currentFacility,
    room:       currentRoom,
    date:       currentDate,
    slot:       selectedSlotLabel
  };
}

async function handleBooking() {
  if (isBooking) return;
  if (!validateInputs()) { showToast("Fix the highlighted fields", "error"); return; }
  if (!currentFacility || !currentRoom || !selectedSlotLabel) { showToast("Complete your slot selection", "error"); return; }

  isBooking = true;
  bookButton.disabled    = true;
  bookButton.classList.add("loading");
  bookButton.textContent = "Booking…";

  try {
    const payload = buildPayload();
    const res     = await fetch("/api/book", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();

    if (!res.ok) {
      showToast(data.error || "Booking failed", "error");
      // If conflict, refresh slots immediately
      if (res.status === 409) {
        renderSlots();
      }
      return;
    }

    showToast("✓ Booking confirmed! Check your email.", "success");

    // Emit booking success for real-time updates
    if (socket && wsConnected) {
      socket.emit('booking_made', {
        booking_id: data.booking_id,
        facility: currentFacility,
        room: currentRoom,
        slot: selectedSlotLabel,
        date: currentDate,
        name: fullNameInput.value.trim()
      });
    }

    selectedSlotLabel = null;  // clear before render so no green flash
    updatePreview();
    updateBookButton();
    await renderSlots();

  } catch (err) {
    showToast("Server error — try again", "error");
  } finally {
    isBooking = false;
    bookButton.classList.remove("loading");
    bookButton.textContent = "CONFIRM BOOKING";
    updateBookButton();
  }
}

bookButton.addEventListener("click", handleBooking);

// ===================== AUTO-REFRESH SLOTS =====================
// Refresh every 30 seconds so all devices stay in sync
setInterval(() => {
  if (currentFacility && currentRoom) renderSlots();
}, 30000);

// ===================== MY BOOKINGS MODAL =====================
function openMyBookingsModal() {
  const email = emailInput.value.trim();
  if (!email.endsWith("@myuct.ac.za")) {
    showToast("Enter your UCT email first", "error");
    return;
  }

  const existing = document.getElementById("myBookingsModal");
  if (existing) existing.remove();

  const modal = document.createElement("div");
  modal.id        = "myBookingsModal";
  modal.className = "otp-modal";
  modal.innerHTML = `
    <div class="otp-modal-inner" style="max-width:480px;width:90vw">
      <div class="otp-title">📋 My Bookings</div>
      <div id="myBookingsList" style="margin-top:1rem;max-height:55vh;overflow-y:auto">
        <div style="text-align:center;color:var(--text-muted);font-size:0.8rem">Loading…</div>
      </div>
      <button class="btn-book" style="margin-top:1rem;background:rgba(255,255,255,0.08);color:var(--text-primary)" onclick="closeMyBookingsModal()">CLOSE</button>
    </div>
  `;

  document.body.appendChild(modal);
  globalOverlay.classList.add("active");
  loadMyBookings(email);
}

function closeMyBookingsModal() {
  const m = document.getElementById("myBookingsModal");
  if (m) m.remove();
  globalOverlay.classList.remove("active");
}

async function loadMyBookings(email) {
  const list = document.getElementById("myBookingsList");
  try {
    const res  = await fetch(`/api/my-bookings?email=${encodeURIComponent(email)}`);
    const data = await res.json();

    if (!data.length) {
      list.innerHTML = `<div style="text-align:center;color:var(--text-muted);font-size:0.8rem;padding:1rem">No bookings found</div>`;
      return;
    }

    list.innerHTML = data.map(b => {
      const statusColor = b.status === 'confirmed' ? 'var(--accent)' : 'var(--text-muted)';

      return `
        <div style="background:rgba(0,0,0,0.3);border-radius:1rem;padding:0.9rem 1rem;margin-bottom:0.6rem;border:1px solid var(--border)">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.4rem">
            <span style="font-weight:600;font-size:0.85rem">${b.date} · ${b.slot}</span>
            <span style="font-size:0.65rem;color:${statusColor};text-transform:uppercase;font-family:'DM Mono',monospace">${b.status}</span>
          </div>
          <div style="font-size:0.72rem;color:var(--text-muted);font-family:'DM Mono',monospace">${b.facility === 'in-house' ? '🏠' : '🏚️'} ${b.room}</div>
          ${b.checked_in ? '<div style="font-size:0.65rem;color:var(--accent);margin-top:0.3rem">✓ Card collected</div>' : ''}
          ${b.checked_out ? '<div style="font-size:0.65rem;color:var(--text-muted);margin-top:0.3rem">✓ Card returned</div>' : ''}
          ${b.late_return ? `<div style="font-size:0.65rem;color:#ff5e6c;margin-top:0.3rem">⚠ Late return: ${b.late_minutes} min</div>` : ''}
        </div>
      `;
    }).join('');
  } catch (err) {
    list.innerHTML = `<div style="text-align:center;color:#ff5e6c;font-size:0.8rem;padding:1rem">Failed to load bookings</div>`;
  }
}

// ===================== INITIALIZATION =====================
// Add WebSocket status indicator
addWSStatusIndicator();

// Initialize WebSocket
initWebSocket();

// Initial render
renderSlots();