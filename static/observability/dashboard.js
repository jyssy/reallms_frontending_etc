const endpoints = {
  activity: "/api/v1/activity/",
  status: "/api/v1/status/",
  tools: "/api/v1/tools/",
};

const byId = (id) => document.getElementById(id);

async function fetchJson(url, timeoutMs = 20000) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(url, {
      headers: { Accept: "application/json" },
      method: "GET",
      signal: controller.signal,
    });
  } finally {
    window.clearTimeout(timeout);
  }
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error?.message || `Request failed (${response.status})`);
  }
  return payload;
}

function renderDiscovery(discovery) {
  byId("model-activity").textContent = String(discovery.model_calls);
  const steps = byId("discovery-steps");
  steps.replaceChildren();
  discovery.steps.forEach((step) => {
    const column = document.createElement("div");
    column.className = "col-md-6 col-xl-3";
    const card = document.createElement("article");
    card.className = "trace-step rounded-3 p-3 h-100";
    const order = document.createElement("p");
    order.className = "trace-order mb-2";
    order.textContent = `STEP ${step.order}`;
    const component = document.createElement("h3");
    component.className = "h6 mb-1";
    component.textContent = step.component;
    const version = document.createElement("p");
    version.className = "small text-secondary mb-2";
    version.textContent = step.version ? `v${step.version}` : "No model invoked";
    const action = document.createElement("p");
    action.className = "small mb-0";
    action.textContent = step.action;
    card.append(order, component, version, action);
    column.append(card);
    steps.append(column);
  });

  const roles = byId("model-roles");
  roles.replaceChildren();
  discovery.model_roles.forEach((role) => {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = role.role;
    const model = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge text-bg-secondary";
    badge.textContent = role.model || "Not observed";
    model.append(badge);
    const purpose = document.createElement("td");
    purpose.className = "text-secondary";
    purpose.textContent = role.purpose;
    row.append(name, model, purpose);
    roles.append(row);
  });
  byId("attribution-note").textContent = discovery.attribution_note;
}

function setConnection(state, label) {
  const dot = byId("connection-dot");
  dot.classList.remove("status-live", "status-error", "status-waiting");
  dot.classList.add(state === "live" ? "status-live" : "status-error");
  byId("connection-label").textContent = label;
}

function renderTools(payload) {
  byId("tool-count").textContent = String(payload.tool_count);
  byId("tool-source").textContent = `Observed from ${payload.server.name}`;
  byId("discovery-latency").textContent = `${payload.latency_ms} ms`;
  setConnection("live", `${payload.server.name} · MCP ${payload.server.protocol_version}`);
  renderDiscovery(payload.discovery);

  const container = byId("tools-container");
  container.replaceChildren();
  const row = document.createElement("div");
  row.className = "row g-3";
  payload.tools.forEach((tool) => {
    const column = document.createElement("div");
    column.className = "col-lg-6";
    const card = document.createElement("article");
    card.className = "tool-card rounded-3 p-3 h-100";
    const name = document.createElement("h3");
    name.className = "tool-name h6 mb-2";
    name.textContent = tool.name;
    const description = document.createElement("p");
    description.className = "small text-secondary mb-0";
    description.textContent = tool.description;
    card.append(name, description);
    column.append(card);
    row.append(column);
  });
  container.append(row);
}

function renderToolError(error) {
  byId("tool-count").textContent = "0";
  byId("tool-source").textContent = "Discovery unavailable";
  byId("discovery-latency").textContent = "—";
  setConnection("error", "MCP unavailable");
  const message = document.createElement("div");
  message.className = "alert alert-warning mb-0";
  message.setAttribute("role", "alert");
  message.textContent = error.message;
  byId("tools-container").replaceChildren(message);
}

function renderActivity(payload) {
  const list = byId("activity-list");
  list.replaceChildren();
  if (!payload.events.length) {
    const empty = document.createElement("li");
    empty.className = "text-secondary";
    empty.textContent = "No activity observed yet.";
    list.append(empty);
    return;
  }
  payload.events.forEach((event) => {
    const item = document.createElement("li");
    item.className = "activity-item";
    const title = document.createElement("div");
    title.className = "d-flex justify-content-between gap-3";
    const type = document.createElement("code");
    type.textContent = event.type;
    const status = document.createElement("span");
    status.className = "small text-secondary";
    status.textContent = event.status;
    title.append(type, status);
    const detail = document.createElement("div");
    detail.className = "small text-secondary mt-1";
    const time = new Date(event.timestamp).toLocaleTimeString();
    const metadata = Object.entries(event.metadata)
      .map(([key, value]) => `${key}: ${value}`)
      .join(" · ");
    detail.textContent = metadata ? `${time} · ${metadata}` : time;
    item.append(title, detail);
    list.append(item);
  });
}

async function refreshTools() {
  const button = byId("refresh-tools");
  button.disabled = true;
  button.textContent = "Discovering…";
  try {
    renderTools(await fetchJson(endpoints.tools));
  } catch (error) {
    renderToolError(error);
  } finally {
    button.disabled = false;
    button.textContent = "Refresh discovery";
    await refreshActivity();
  }
}

async function refreshActivity() {
  try {
    renderActivity(await fetchJson(endpoints.activity));
  } catch {
    // Keep the last known activity; status is already visible elsewhere.
  }
}

async function initialize() {
  try {
    const status = await fetchJson(endpoints.status);
    const label = status.mcp.configured ? "MCP configured" : "MCP not configured";
    setConnection(status.mcp.configured ? "live" : "error", label);
  } catch {
    setConnection("error", "Frontend API unavailable");
  }
  await refreshTools();
  window.setInterval(refreshActivity, 3000);
}

byId("refresh-tools").addEventListener("click", refreshTools);
initialize();
