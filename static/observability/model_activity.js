const modelEndpoint = "/api/v1/observability/models/";
const byId = (id) => document.getElementById(id);
const visibleModelRoles = new Set(["router", "reviewer", "judge"]);
const timestampOptions = {
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
  timeZoneName: "short",
};

function formatTimestamp(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  const eastern = new Intl.DateTimeFormat("en-US", {
    ...timestampOptions,
    timeZone: "America/New_York",
  }).format(date);
  const utc = new Intl.DateTimeFormat("en-US", {
    ...timestampOptions,
    timeZone: "UTC",
  }).format(date);
  return `${eastern} · ${utc}`;
}

async function fetchModelActivity() {
  const response = await fetch(modelEndpoint, {
    headers: { Accept: "application/json" },
    method: "GET",
  });
  if (!response.ok) {
    throw new Error("model_activity_unavailable");
  }
  return response.json();
}

function labelRole(role) {
  return role.charAt(0).toUpperCase() + role.slice(1);
}

function statePill(state) {
  const pill = document.createElement("span");
  pill.className = `state-pill state-${state}`;
  pill.textContent = state;
  return pill;
}

function renderModels(payload) {
  const source =
    payload.source === "synthetic_fixture"
      ? "Demo fixture · no live models"
      : payload.source === "sqlite_history"
        ? "SQLite history"
        : "Live process";
  byId("model-source").textContent = source;

  const body = byId("model-activity-body");
  body.replaceChildren();
  payload.models
    .filter((model) => visibleModelRoles.has(model.role))
    .forEach((model) => {
      const row = document.createElement("tr");
      const role = document.createElement("th");
      role.scope = "row";
      role.textContent = labelRole(model.role);
      const configured = document.createElement("td");
      configured.textContent = model.configured_model || "Not supplied to this frontend";
      const observed = document.createElement("td");
      observed.textContent =
        payload.source === "synthetic_fixture"
          ? "Demo placeholder — no live model ran"
          : model.observed_models.length
            ? model.observed_models.join(", ")
            : model.event_count > 0
              ? "Activity observed · identity not reported"
              : "Not observed by this frontend";
      const states = document.createElement("td");
      states.className = "state-cell";
      if (payload.source === "synthetic_fixture") {
        if (model.configured_model) {
          states.append(statePill("configured"));
        }
        states.append(statePill("not-reported"));
        states.lastElementChild.textContent = "demo only";
      } else if (model.event_count > 0 && !model.observed_models.length) {
        if (model.configured_model) {
          states.append(statePill("configured"));
        }
        const activity = statePill("observed");
        activity.textContent = "activity observed";
        const identity = statePill("not-reported");
        identity.textContent = "identity not reported";
        states.append(activity, identity);
      } else if (model.states.length) {
        model.states.forEach((state) => states.append(statePill(state)));
      } else {
        states.textContent = "No evidence";
        states.classList.add("text-secondary");
      }
      const count = document.createElement("td");
      count.textContent =
        payload.source === "synthetic_fixture"
          ? `demo · ${model.event_count}`
          : String(model.event_count);
      row.append(role, configured, observed, states, count);
      body.append(row);
    });

  const cards = byId("provider-cards");
  cards.replaceChildren();
  payload.providers.forEach((provider) => {
    const column = document.createElement("div");
    column.className = "col-md-6";
    const card = document.createElement("article");
    card.className = "provider-card rounded-3 p-3 h-100";
    const title = document.createElement("h3");
    title.className = "h6 text-capitalize";
    title.textContent = `${provider.provider} provider`;
    const detail = document.createElement("p");
    detail.className = "small text-secondary mb-0";
    detail.textContent = provider.observed
      ? `${provider.attempts} attempts · ${provider.retries} retries${provider.degraded ? " · degraded" : ""}`
      : "Not observed in retained runs";
    card.append(title, detail);
    column.append(card);
    cards.append(column);
  });
  byId("model-refresh-status").textContent = `Updated ${formatTimestamp(new Date())}`;
}

async function refreshModels() {
  try {
    renderModels(await fetchModelActivity());
  } catch {
    byId("model-refresh-status").textContent = "Updates unavailable; showing last known state.";
  }
}

refreshModels();
window.setInterval(refreshModels, 3000);
