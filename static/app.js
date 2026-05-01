// ===================== STATE =====================
let currentFacility    = null;
let currentRoom        = null;
let currentDate        = new Date().toISOString().slice(0, 10);
let selectedSlotLabel  = null;
let isLoadingSlots     = false;
let isBooking          = false;
let machineWorking     = true;
let machineNote        = "";

// WebSocket state
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
dateInput.max   = today;
dateInput.disabled = true;
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
        if (currentFacility && currentRoom) renderSlots();
    });

    socket.on('connect_error', (error) => {
        console.error('WebSocket connection error:', error);
        wsConnected = false;
        updateWSStatus(false);
    });

    socket.on('slots_updated', (data) => {
        if (data.facility === currentFacility &&
            data.room === currentRoom &&
            data.date === currentDate) {
            renderSlots();
        }
    });

    socket.on('machine_updated', (data) => {
        if (data.machine && data.machine.facility === currentFacility &&
            data.machine.room === currentRoom) {
            machineWorking = data.machine.working;
            machineNote = data.machine.note || "";
            renderSlots();
            showToast(data.machine.working ? '✅ Machine is now available' : '⚠️ Machine is out of service', 'info');
        }
    });
}

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
    `;
    indicator.innerHTML = '🔌 Connecting...';
    document.body.appendChild(indicator);
}

function getBookingWindowStatus(facility) {
    const openHour = 6;
    const now = new Date();
    if (now.getHours() < openHour) {
        return { open: false, msg: `Bookings open at 06:00 AM` };
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

// ===================== SELECTION DISPLAY (for the new card) =====================
function updateSelectionDisplay() {
    const container = document.getElementById('selectionContent');
    if (!container) return;

    if (!currentFacility && !currentRoom && !selectedSlotLabel) {
        container.innerHTML = `
            <div class="selection-empty">
                <span class="empty-icon">👆</span>
                <p>Select facility & room above</p>
            </div>
        `;
        return;
    }

    let html = '';
    if (currentFacility) {
        html += `
            <div class="selection-item">
                <span class="selection-label">FACILITY</span>
                <span class="selection-value">${FACILITY_CONFIG[currentFacility].display}</span>
            </div>
        `;
    }
    if (currentRoom) {
        html += `
            <div class="selection-item">
                <span class="selection-label">ROOM</span>
                <span class="selection-value">${currentRoom}</span>
            </div>
        `;
    }
    if (currentDate) {
        const displayDate = new Date(currentDate).toLocaleDateString('en-ZA', { weekday: 'short', month: 'short', day: 'numeric' });
        html += `
            <div class="selection-item">
                <span class="selection-label">DATE</span>
                <span class="selection-value">${displayDate}</span>
            </div>
        `;
    }
    if (selectedSlotLabel) {
        html += `
            <div class="selection-item">
                <span class="selection-label">TIME SLOT</span>
                <span class="selection-value">${selectedSlotLabel}</span>
            </div>
        `;
    }

    if (selectedSlotLabel) {
        html += `
            <div class="selection-booking-id">
                ⚡ Ready to book this slot
            </div>
        `;
    }

    container.innerHTML = html;
}

// ===================== RESET ALL SELECTIONS =====================
window.resetAllSelection = function() {
    currentFacility = null;
    currentRoom = null;
    selectedSlotLabel = null;
    machineWorking = true;
    machineNote = "";
    facilitySelectedSpan.textContent = "Select facility";
    roomSelectedSpan.textContent = "— First choose facility —";
    roomTrigger.setAttribute('data-locked', 'true');
    updateBookButton();
    updateSelectionDisplay();
    renderSlots();
    showToast("Selection cleared", "info");
};

// ===================== DROPDOWNS & SELECTION =====================
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

function selectFacility(key) {
    facilityTrigger.classList.add('loading');
    currentFacility = key;
    currentRoom = null;
    selectedSlotLabel = null;
    machineWorking = true;
    machineNote = "";
    facilitySelectedSpan.textContent = FACILITY_CONFIG[key].display;
    roomSelectedSpan.textContent = "Select room";
    roomTrigger.removeAttribute('data-locked');
    updateBookButton();
    updateSelectionDisplay();
    renderSlots().finally(() => {
        facilityTrigger.classList.remove('loading');
    });
}

function selectRoom(roomName) {
    roomTrigger.classList.add('loading');
    currentRoom = roomName;
    selectedSlotLabel = null;
    roomSelectedSpan.textContent = roomName;
    updateBookButton();
    updateSelectionDisplay();
    renderSlots().finally(() => {
        roomTrigger.classList.remove('loading');
    });
}

function selectSlot(slot) {
    selectedSlotLabel = slot;
    document.querySelectorAll('.slot-node').forEach(node => {
        node.classList.toggle("selected-slot", node.dataset.slot === slot);
    });
    updateBookButton();
    updateSelectionDisplay();
}

dateInput.addEventListener("change", (e) => {
    currentDate = e.target.value;
    selectedSlotLabel = null;
    updateBookButton();
    updateSelectionDisplay();
    renderSlots();
});

// ===================== API & SLOTS RENDERING =====================
async function fetchAvailability() {
    const res = await fetch(`/api/availability?facility=${encodeURIComponent(currentFacility)}&room=${encodeURIComponent(currentRoom)}&date=${currentDate}&_t=${Date.now()}`);
    if (!res.ok) throw new Error("Network error");
    return await res.json();
}

function renderSkeletons() {
    slotsContainer.innerHTML = "";
    const count = currentFacility ? FACILITY_CONFIG[currentFacility].getTimeSlots().length : 5;
    for (let i = 0; i < count; i++) {
        const sk = document.createElement("div");
        sk.className = "slot-skeleton";
        sk.style.animationDelay = `${i * 0.05}s`;
        slotsContainer.appendChild(sk);
    }
}

async function renderSlots() {
    if (!currentFacility || !currentRoom) {
        slotsContainer.innerHTML = `<div class="empty-state">Select facility &amp; room to view slots</div>`;
        removeMachineBanner();
        return;
    }

    const windowStatus = getBookingWindowStatus(currentFacility);
    if (!windowStatus.open) {
        slotsContainer.innerHTML = `<div class="empty-state">🔒 ${windowStatus.msg}</div>`;
        removeMachineBanner();
        return;
    }

    if (isLoadingSlots) return;
    isLoadingSlots = true;
    renderSkeletons();

    try {
        const data = await fetchAvailability();
        const takenSlots = data.taken || [];
        machineWorking = data.machine_working !== false;
        machineNote = data.machine_note || "";

        if (!machineWorking) {
            showMachineBanner(machineNote);
        } else {
            removeMachineBanner();
        }

        const slots = FACILITY_CONFIG[currentFacility].getTimeSlots();

        if (selectedSlotLabel && takenSlots.includes(selectedSlotLabel)) {
            selectedSlotLabel = null;
            updateBookButton();
            updateSelectionDisplay();
            showToast("Your selected slot was just taken — please choose another", "error");
        }

        slotsContainer.innerHTML = "";

        slots.forEach((slot, i) => {
            const isTaken = takenSlots.includes(slot) || !machineWorking;
            const isSelected = !isTaken && selectedSlotLabel === slot;
            const div = document.createElement("div");

            div.className = `slot-node ${isTaken ? "unavailable" : "available"}${isSelected ? " selected-slot" : ""}`;
            div.dataset.slot = slot;
            div.style.animationDelay = `${i * 40}ms`;
            div.textContent = slot;

            if (!isTaken) {
                div.onclick = () => selectSlot(slot);
                div.onmouseenter = () => { if (!isTaken) div.style.transform = "translateY(-2px)"; };
                div.onmouseleave = () => { div.style.transform = ""; };
            }
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
    banner.id = "machineBanner";
    banner.className = "machine-banner";
    banner.innerHTML = `⚠ Machine out of service${note ? `: ${note}` : ""}`;
    slotsContainer.parentElement.insertBefore(banner, slotsContainer);
}

function removeMachineBanner() {
    const b = document.getElementById("machineBanner");
    if (b) b.remove();
}

// ===================== VALIDATION & BOOKING =====================
function validateInputs() {
    let ok = true;
    const name = fullNameInput.value.trim();
    const email = emailInput.value.trim();
    const room = roomNumberInput.value.trim();

    if (!name) { fullNameInput.classList.add("invalid"); ok = false; }
    else fullNameInput.classList.remove("invalid");

    if (!email.endsWith("@myuct.ac.za")) { emailInput.classList.add("invalid"); ok = false; }
    else emailInput.classList.remove("invalid");

    if (!room) { roomNumberInput.classList.add("invalid"); ok = false; }
    else roomNumberInput.classList.remove("invalid");

    return ok;
}

emailInput.addEventListener("input", () => {
    if (emailInput.value.endsWith("@myuct.ac.za")) emailInput.classList.remove("invalid");
});

function buildPayload() {
    return {
        name: fullNameInput.value.trim(),
        email: emailInput.value.trim().toLowerCase(),
        roomNumber: roomNumberInput.value.trim(),
        facility: currentFacility,
        room: currentRoom,
        date: currentDate,
        slot: selectedSlotLabel
    };
}

async function handleBooking() {
    if (isBooking) return;
    if (!validateInputs()) { showToast("Fix the highlighted fields", "error"); return; }
    if (!currentFacility || !currentRoom || !selectedSlotLabel) { showToast("Complete your slot selection", "error"); return; }

    isBooking = true;
    bookButton.disabled = true;
    bookButton.classList.add("loading");
    bookButton.textContent = "Booking…";

    try {
        const payload = buildPayload();
        const res = await fetch("/api/book", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (!res.ok) {
            showToast(data.error || "Booking failed", "error");
            if (res.status === 409) {
                await renderSlots();
            }
            return;
        }

        showToast(`✓ Booking confirmed! Your Booking ID: #${data.booking_id}`, "success");

        selectedSlotLabel = null;
        updateBookButton();
        updateSelectionDisplay();
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

// Auto-refresh slots every 30 seconds
setInterval(() => {
    if (currentFacility && currentRoom) renderSlots();
}, 30000);

// ===================== ENHANCED MY BOOKINGS MODAL =====================
function openMyBookingsModal() {
    const email = emailInput.value.trim();
    if (!email.endsWith("@myuct.ac.za")) {
        showToast("Enter your UCT email first", "error");
        return;
    }

    const existing = document.getElementById("myBookingsModal");
    if (existing) existing.remove();

    const modal = document.createElement("div");
    modal.id = "myBookingsModal";
    modal.innerHTML = `
        <div class="booking-modal">
            <div class="modal-header">
                <h3>📋 My Laundry Bookings</h3>
                <button class="modal-close" onclick="closeMyBookingsModal()">✕</button>
            </div>
            <div id="myBookingsList" class="bookings-list-modal">
                <div class="loading-spinner">Loading your bookings...</div>
            </div>
            <div class="modal-footer">
                <small>💡 Save your Booking ID to check status</small>
                <button class="btn-refresh" onclick="refreshMyBookings()">🔄 Refresh</button>
                <button class="btn-close-modal" onclick="closeMyBookingsModal()">Close</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    globalOverlay.classList.add("active");
    loadMyBookingsEnhanced(email);
}

window.closeMyBookingsModal = function() {
    const modal = document.getElementById("myBookingsModal");
    if (modal) modal.remove();
    globalOverlay.classList.remove("active");
};

window.refreshMyBookings = function() {
    const email = emailInput.value.trim();
    if (email.endsWith("@myuct.ac.za")) {
        loadMyBookingsEnhanced(email);
    }
};

async function loadMyBookingsEnhanced(email) {
    const container = document.getElementById("myBookingsList");
    if (!container) return;

    try {
        const res = await fetch(`/api/my-bookings?email=${encodeURIComponent(email)}`);
        const bookings = await res.json();

        if (!bookings.length) {
            container.innerHTML = `<div class="empty-bookings">📭 No bookings found. Make your first booking!</div>`;
            return;
        }

        container.innerHTML = bookings.map(b => {
            let statusColor = "#ffa500";
            let statusIcon = "⏳";
            let statusText = "";

            if (b.checked_in && !b.checked_out) {
                statusColor = "#00e5b0";
                statusIcon = "🧺";
                statusText = "Card Collected - Laundry in Progress";
            } else if (b.checked_out) {
                statusColor = "#7a8aaa";
                statusIcon = "✅";
                statusText = b.late_return ? `Completed (LATE +${b.late_minutes} min)` : "Completed - Card Returned";
            } else if (b.status === "abandoned") {
                statusColor = "#ff5e6c";
                statusIcon = "❌";
                statusText = "Abandoned - No Show";
            } else {
                statusText = "Waiting for Check-in";
            }

            return `
                <div class="booking-card-modal" style="border-left-color: ${statusColor}">
                    <div class="booking-time">⏰ ${b.slot}</div>
                    <div class="booking-date">📅 ${b.date}</div>
                    <div class="booking-room">🏠 ${b.facility === 'in-house' ? 'In-House' : 'Basement'} · ${b.room}</div>
                    <div class="booking-status" style="color: ${statusColor}">
                        ${statusIcon} ${statusText}
                    </div>
                    <div class="booking-id">🆔 Booking ID: #${b.id}</div>
                </div>
            `;
        }).join('');
    } catch (err) {
        container.innerHTML = `<div class="error-message">❌ Failed to load bookings. Try again.</div>`;
    }
}

// ===================== INITIALIZATION =====================
addWSStatusIndicator();
initWebSocket();
renderSlots();