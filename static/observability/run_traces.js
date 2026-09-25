const runsEndpoint = "/api/v1/observability/runs/";
const byId = (id) => document.getElementById(id);
let selectedRunId = null;

async function fetchRuns() {
  const response = await fetch(runsEndpoint, {
    headers: { Accept: "application/json" },
    method: "GET",
  });
  if (!response.ok) {
    throw new Error("run_traces_unavailable");
  }
  return response.json();
}

function statusPill(status) {
  const pill = document.createElement("span");
  pill.className = `trace-status trace-status-${status}`;
  pill.textContent = status;
  return pill;
}

function renderEvents(run) {
  byId("trace-title").textContent = `Run ${run.run_id.slice(0, 8)}`;
  byId("trace-lifecycle").textContent = run.lifecycle.replace("_", " ");
  const body = byId("trace-events");
  body.replaceChildren();
  run.events.forEach((event) => {
    const row = document.createElement("tr");
    const sequence = document.createElement("td");
    sequence.textContent = String(event.sequence);
    const timestamp = document.createElement("td");
    timestamp.textContent = new Date(event.timestamp).toLocaleTimeString();
    const component = document.createElement("td");
    component.textContent = event.component;
    const eventType = document.createElement("td");
    const code = document.createElement("code");
    code.textContent = event.event_type;
    eventType.append(code);
    const status = document.createElement("td");
    status.append(statusPill(event.status));
    const metadata = document.createElement("td");
    metadata.className = "small text-secondary";
    const values = Object.entries(event.metadata).map(([key, value]) => `${key}: ${value}`);
    metadata.textContent = values.length ? values.join(" · ") : "—";
    row.append(sequence, timestamp, component, eventType, status, metadata);
    body.append(row);
  });
}

function renderEmpty() {
  selectedRunId = null;
  byId("trace-title").textContent = "No run selected";
  byId("trace-lifecycle").textContent = "waiting";
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = 6;
  cell.className = "text-secondary";
  cell.textContent = "No observed runs are retained in this process.";
  row.append(cell);
  byId("trace-events").replaceChildren(row);
}

function renderRuns(payload) {
  byId("trace-source").textContent =
    payload.source === "synthetic_fixture" ? "Synthetic fixture" : "Live process";
  byId("run-count").textContent = String(payload.runs.length);
  byId("event-count").textContent = String(payload.retention.stored_events);
  byId("dropped-count").textContent = String(payload.retention.dropped_events);

  if (!payload.runs.some((run) => run.run_id === selectedRunId)) {
    selectedRunId = payload.runs[0]?.run_id || null;
  }
  const list = byId("run-list");
  list.replaceChildren();
  if (!payload.runs.length) {
    const empty = document.createElement("p");
    empty.className = "text-secondary p-2 mb-0";
    empty.textContent = "No observed runs yet.";
    list.append(empty);
    renderEmpty();
  } else {
    payload.runs.forEach((run) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "run-button";
      button.setAttribute("aria-pressed", String(run.run_id === selectedRunId));
      const name = document.createElement("span");
      name.className = "font-monospace";
      name.textContent = run.run_id.slice(0, 8);
      const summary = document.createElement("span");
      summary.className = "small text-secondary";
      summary.textContent = `${run.lifecycle.replace("_", " ")} · ${run.event_count} events`;
      button.append(name, summary);
      button.addEventListener("click", () => {
        selectedRunId = run.run_id;
        renderRuns(payload);
      });
      list.append(button);
    });
    renderEvents(payload.runs.find((run) => run.run_id === selectedRunId));
  }
  byId("trace-refresh-status").textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

async function refreshRuns() {
  try {
    renderRuns(await fetchRuns());
  } catch {
    byId("trace-refresh-status").textContent = "Updates unavailable; showing last known trace.";
  }
}

refreshRuns();
window.setInterval(refreshRuns, 2500);
