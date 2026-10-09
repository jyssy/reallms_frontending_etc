const endpoints = {
  activity: "/api/v1/activity/",
  models: "/api/v1/observability/models/",
  status: "/api/v1/status/",
  tools: "/api/v1/tools/",
};

const byId = (id) => document.getElementById(id);
const actorRoles = new Set(["router", "reviewer", "judge"]);
const modelStates = new Set(["configured", "observed", "skipped", "fallback", "not-reported"]);
const actorPurposes = {
  router: "Classifies the request and selects the workflow.",
  reviewer: "Produces the role-specific reviewed result.",
  judge: "Evaluates the result and decides whether revision is needed.",
};
const timestampOptions = {
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
  timeZoneName: "short",
};

function formatTimestamp(value, includeDate = false) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  const options = includeDate
    ? { ...timestampOptions, year: "numeric", month: "short", day: "numeric" }
    : timestampOptions;
  const eastern = new Intl.DateTimeFormat("en-US", {
    ...options,
    timeZone: "America/New_York",
  }).format(date);
  const utc = new Intl.DateTimeFormat("en-US", {
    ...options,
    timeZone: "UTC",
  }).format(date);
  return `${eastern} · ${utc}`;
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isSafeModelName(value) {
  return (
    value === null ||
    (typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}$/.test(value))
  );
}

function isSafeModelActivity(payload) {
  return (
    isPlainObject(payload) &&
    payload.contract_version === 1 &&
    ["live_process", "sqlite_history", "synthetic_fixture"].includes(payload.source) &&
    typeof payload.observed === "boolean" &&
    Array.isArray(payload.models) &&
    payload.models.length === actorRoles.size &&
    new Set(payload.models.map((model) => model.role)).size === actorRoles.size &&
    payload.models.every(
      (model) =>
        isPlainObject(model) &&
        actorRoles.has(model.role) &&
        isSafeModelName(model.configured_model) &&
        Array.isArray(model.observed_models) &&
        model.observed_models.length <= 24 &&
        model.observed_models.every((name) => name !== null && isSafeModelName(name)) &&
        Array.isArray(model.states) &&
        model.states.every((state) => modelStates.has(state)) &&
        Number.isInteger(model.event_count) &&
        model.event_count >= 0 &&
        model.event_count <= 512,
    )
  );
}

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
}

function modelStatePill(state, label = state) {
  const pill = document.createElement("span");
  pill.className = `state-pill state-${state}`;
  pill.textContent = label;
  return pill;
}

function renderModelActivity(payload) {
  const synthetic = payload.source === "synthetic_fixture";
  const observedNames = synthetic
    ? []
    : [...new Set(payload.models.flatMap((model) => model.observed_models))];
  byId("model-activity").textContent = String(observedNames.length);
  byId("model-activity-detail").textContent = synthetic
    ? "Demo fixture · no live models ran"
    : observedNames.length
      ? `${observedNames.length} identities observed in retained runs`
      : "No model identities captured by this frontend";

  const roles = byId("model-roles");
  roles.replaceChildren();
  payload.models.forEach((role) => {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = role.role.charAt(0).toUpperCase() + role.role.slice(1);
    const configured = document.createElement("td");
    configured.textContent = role.configured_model || "Not configured";
    const observed = document.createElement("td");
    observed.textContent = synthetic
      ? "Demo placeholder — no live model ran"
      : role.observed_models.length
        ? role.observed_models.join(", ")
        : role.event_count > 0
          ? "Activity observed · identity not reported"
          : "Not observed by this frontend";
    const evidence = document.createElement("td");
    evidence.className = "state-cell";
    if (synthetic) {
      if (role.configured_model) {
        evidence.append(modelStatePill("configured"));
      }
      evidence.append(modelStatePill("not-reported", "demo only"));
    } else if (role.event_count > 0 && !role.observed_models.length) {
      if (role.configured_model) {
        evidence.append(modelStatePill("configured"));
      }
      evidence.append(
        modelStatePill("observed", "activity observed"),
        modelStatePill("not-reported", "identity not reported"),
      );
    } else if (role.states.length) {
      role.states.forEach((state) => evidence.append(modelStatePill(state)));
    } else {
      evidence.textContent = "No evidence";
      evidence.classList.add("text-secondary");
    }
    const purpose = document.createElement("td");
    purpose.className = "text-secondary";
    purpose.textContent = actorPurposes[role.role];
    row.append(name, configured, observed, evidence, purpose);
    roles.append(row);
  });
  byId("attribution-note").textContent = synthetic
    ? "This is a deterministic UI fixture. It does not describe a real orchestration call."
    : "Configured expectations come from the frontend's safe server-side catalog. Observed names appear only for model-backed calls captured through this frontend's observed MCP adapter; calls from other MCP clients are outside this process."
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
    const time = formatTimestamp(event.timestamp);
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

async function refreshModelActivity() {
  try {
    const payload = await fetchJson(endpoints.models);
    if (!isSafeModelActivity(payload)) {
      throw new Error("Invalid model activity payload");
    }
    renderModelActivity(payload);
  } catch {
    byId("model-activity-detail").textContent =
      "Model projection unavailable; showing last known state";
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
  await Promise.all([refreshTools(), refreshModelActivity()]);
  window.setInterval(refreshActivity, 3000);
  window.setInterval(refreshModelActivity, 3000);
}

byId("refresh-tools").addEventListener("click", refreshTools);
initialize();
