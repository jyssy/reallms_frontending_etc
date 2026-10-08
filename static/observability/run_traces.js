const runsEndpoint = "/api/v1/observability/runs/";
const modelsEndpoint = "/api/v1/observability/models/";
const byId = (id) => document.getElementById(id);
const stageIds = ["route", "retrieve", "review", "judge", "revise", "complete"];
const modelRoles = new Set(["router", "embedding", "reranker", "reviewer", "judge"]);
const visibleModelRoles = new Set(["router", "reviewer", "judge"]);
const stageStatuses = new Set([
  "pending",
  "not_run",
  "started",
  "success",
  "degraded",
  "failed",
  "skipped",
  "retrying",
]);
const modelStates = new Set(["configured", "observed", "skipped", "fallback", "not-reported"]);
const eventTypes = new Set([
  "run.started",
  "router.completed",
  "retrieval.completed",
  "embedding.completed",
  "reranking.completed",
  "specialist.completed",
  "judge_critique.completed",
  "revision.completed",
  "revision.skipped",
  "provider.attempt",
  "provider.retry",
  "run.completed",
]);
const components = new Set([
  "pipeline",
  "router",
  "retrieval",
  "embedding",
  "reranker",
  "specialist",
  "judge",
  "revision",
  "provider",
]);
const eventStatuses = new Set(["started", "success", "degraded", "failed", "skipped", "retrying"]);
const resultStatuses = new Set([
  "success",
  "degraded_success",
  "unavailable_dependency",
  "invalid_configuration",
  "invalid_input",
  "security_block",
  "internal_failure",
]);
const taskTypes = new Set(["coding", "general", "ops", "search"]);
let selectedRunId = null;
let configuredModelsByRole = new Map();
let executorContext = { name: null, state: "not-reported", scope: "external_client" };
let traceSource = "live_process";

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isSafeModel(model) {
  return (
    isPlainObject(model) &&
    modelRoles.has(model.role) &&
    (model.name === null ||
      (typeof model.name === "string" &&
        /^[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}$/.test(model.name))) &&
    modelStates.has(model.state)
  );
}

function isSafeModelName(value) {
  return (
    value === null ||
    (typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}$/.test(value))
  );
}

function isSafeModelActivityPayload(payload) {
  return (
    isPlainObject(payload) &&
    payload.contract_version === 1 &&
    ["live_process", "sqlite_history", "synthetic_fixture"].includes(payload.source) &&
    Array.isArray(payload.models) &&
    payload.models.length === modelRoles.size &&
    payload.models.every(
      (model) =>
        isPlainObject(model) &&
        modelRoles.has(model.role) &&
        isSafeModelName(model.configured_model) &&
        Array.isArray(model.observed_models) &&
        model.observed_models.length <= 24 &&
        model.observed_models.every((name) => name !== null && isSafeModelName(name)) &&
        Array.isArray(model.states) &&
        model.states.every((state) => modelStates.has(state)) &&
        Number.isInteger(model.event_count) &&
        model.event_count >= 0 &&
        model.event_count <= 512,
    ) &&
    isPlainObject(payload.executor) &&
    isSafeModelName(payload.executor.name) &&
    ["configured", "not-reported"].includes(payload.executor.state) &&
    payload.executor.scope === "external_client"
  );
}

function isNullableCount(value) {
  return value === null || (Number.isInteger(value) && value >= 0 && value <= 1000000);
}

function isSafeSummary(summary) {
  return (
    isPlainObject(summary) &&
    (summary.started_at === null ||
      (typeof summary.started_at === "string" && !Number.isNaN(Date.parse(summary.started_at)))) &&
    (summary.completed_at === null ||
      (typeof summary.completed_at === "string" && !Number.isNaN(Date.parse(summary.completed_at)))) &&
    isNullableCount(summary.total_duration_ms) &&
    isNullableCount(summary.reported_stage_duration_ms) &&
    isNullableCount(summary.reported_stage_count) &&
    isNullableCount(summary.provider_attempts) &&
    isNullableCount(summary.provider_retries) &&
    Array.isArray(summary.providers) &&
    summary.providers.length <= 2 &&
    summary.providers.every((provider) => ["local", "remote"].includes(provider)) &&
    typeof summary.fallback === "boolean" &&
    isPlainObject(summary.retrieval) &&
    [summary.retrieval.retrieval_used, summary.retrieval.context_used].every(
      (value) => value === null || typeof value === "boolean",
    ) &&
    isNullableCount(summary.retrieval.candidate_count) &&
    isNullableCount(summary.retrieval.selected_count) &&
    isPlainObject(summary.resources) &&
    isNullableCount(summary.resources.embedding_batch_size) &&
    isNullableCount(summary.resources.rerank_candidate_count) &&
    isNullableCount(summary.resources.rerank_selected_count)
  );
}

function isSafeRunModel(model) {
  return (
    isSafeModel(model) &&
    Number.isInteger(model.event_count) &&
    model.event_count >= 0 &&
    model.event_count <= 128
  );
}

function isSafeStage(stage, expectedId) {
  return (
    isPlainObject(stage) &&
    stage.id === expectedId &&
    typeof stage.label === "string" &&
    stage.label.length <= 32 &&
    typeof stage.purpose === "string" &&
    stage.purpose.length <= 160 &&
    stageStatuses.has(stage.status) &&
    Number.isInteger(stage.event_count) &&
    stage.event_count >= 0 &&
    stage.event_count <= 128 &&
    (stage.duration_ms === null || (Number.isInteger(stage.duration_ms) && stage.duration_ms >= 0)) &&
    Array.isArray(stage.models) &&
    stage.models.length <= 2 &&
    stage.models.every(isSafeModel)
  );
}

function isSafeEvent(event) {
  return (
    isPlainObject(event) &&
    Number.isInteger(event.sequence) &&
    event.sequence > 0 &&
    typeof event.timestamp === "string" &&
    !Number.isNaN(Date.parse(event.timestamp)) &&
    eventTypes.has(event.event_type) &&
    components.has(event.component) &&
    eventStatuses.has(event.status) &&
    isPlainObject(event.metadata) &&
    Object.keys(event.metadata).length <= 20 &&
    Object.values(event.metadata).every((value) =>
      ["string", "number", "boolean"].includes(typeof value),
    )
  );
}

function isSafeResult(result) {
  if (result === null) {
    return true;
  }
  if (
    !isPlainObject(result) ||
    !resultStatuses.has(result.status) ||
    !(result.task_type === null || taskTypes.has(result.task_type)) ||
    !isPlainObject(result.model_roles)
  ) {
    return false;
  }
  return Object.entries(result.model_roles).every(
    ([role, name]) =>
      visibleModelRoles.has(role) && typeof name === "string" && name.length <= 128,
  );
}

function isSafeRun(run) {
  return (
    isPlainObject(run) &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(
      run.run_id,
    ) &&
    ["complete", "in_progress"].includes(run.lifecycle) &&
    eventStatuses.has(run.status) &&
    isSafeResult(run.result) &&
    isSafeSummary(run.summary) &&
    Array.isArray(run.models) &&
    run.models.length === 5 &&
    run.models.every(isSafeRunModel) &&
    Array.isArray(run.workflow) &&
    run.workflow.length === stageIds.length &&
    run.workflow.every((stage, index) => isSafeStage(stage, stageIds[index])) &&
    Array.isArray(run.events) &&
    run.events.length <= 128 &&
    run.events.every(isSafeEvent)
  );
}

function formatDuration(duration) {
  if (duration === null) {
    return "Not reported";
  }
  if (duration < 1000) {
    return `${duration} ms`;
  }
  if (duration < 60000) {
    return `${(duration / 1000).toFixed(2)} s`;
  }
  return `${(duration / 60000).toFixed(2)} min`;
}

function formatRole(role) {
  return role.charAt(0).toUpperCase() + role.slice(1);
}

function isSafePayload(payload) {
  return (
    isPlainObject(payload) &&
    payload.contract_version === 1 &&
    ["live_process", "sqlite_history", "synthetic_fixture"].includes(payload.source) &&
    isPlainObject(payload.retention) &&
    Number.isInteger(payload.retention.stored_events) &&
    Number.isInteger(payload.retention.dropped_events) &&
    Array.isArray(payload.runs) &&
    payload.runs.length <= 24 &&
    payload.runs.every(isSafeRun)
  );
}

async function fetchRuns() {
  const response = await fetch(runsEndpoint, {
    headers: { Accept: "application/json" },
    method: "GET",
  });
  if (!response.ok) {
    throw new Error("run_traces_unavailable");
  }
  const payload = await response.json();
  if (!isSafePayload(payload)) {
    throw new Error("invalid_run_trace_payload");
  }
  return payload;
}

async function fetchModelActivity() {
  const response = await fetch(modelsEndpoint, {
    headers: { Accept: "application/json" },
    method: "GET",
  });
  if (!response.ok) {
    throw new Error("model_activity_unavailable");
  }
  const payload = await response.json();
  if (!isSafeModelActivityPayload(payload)) {
    throw new Error("invalid_model_activity_payload");
  }
  return payload;
}

function statusPill(status) {
  const pill = document.createElement("span");
  pill.className = `trace-status trace-status-${status}`;
  pill.textContent = status;
  return pill;
}

function displayModelName(model) {
  if (
    traceSource === "synthetic_fixture" &&
    typeof model?.name === "string" &&
    model.name.startsWith("synthetic-")
  ) {
    return "Demo placeholder — no live model ran";
  }
  return model?.name || "Model not reported";
}

function attributionLabel(model) {
  if (model.state === "configured") {
    return `${model.role}: ${model.name} · configured, not confirmed`;
  }
  if (model.state === "not-reported") {
    return `${model.role}: model not reported`;
  }
  if (model.state === "skipped") {
    return `${model.role}: skipped`;
  }
  return `${model.role}: ${displayModelName(model)} · ${model.state}`;
}

function actorNode(run, { label, order, role, stageId }) {
  const item = document.createElement("li");
  item.className = "actor-node";
  const stage = run.workflow.find((candidate) => candidate.id === stageId);
  const model = run.models.find((candidate) => candidate.role === role);
  const configuredModel = configuredModelsByRole.get(role);

  const header = document.createElement("div");
  header.className = "d-flex justify-content-between align-items-start gap-2";
  const heading = document.createElement("div");
  const index = document.createElement("span");
  index.className = "actor-order";
  index.textContent = String(order);
  const name = document.createElement("h3");
  name.className = "h6 mb-0";
  name.textContent = label;
  heading.append(index, name);
  header.append(heading, statusPill(stage?.status || "pending"));

  const identity = document.createElement("p");
  identity.className = "actor-model mt-3 mb-1";
  identity.textContent = configuredModel || displayModelName(model);

  const evidence = document.createElement("p");
  evidence.className = "small text-secondary mb-0";
  const sourceLabel = traceSource === "synthetic_fixture" ? "Fixture" : "Trace";
  if (
    configuredModel &&
    model?.name &&
    model.name !== configuredModel &&
    ["observed", "fallback"].includes(model.state)
  ) {
    evidence.textContent =
      traceSource === "synthetic_fixture"
        ? `Demo scenario · ${model.state} · no live model ran`
        : `${sourceLabel} identity: ${model.name} · ${model.state}`;
  } else if (configuredModel) {
    evidence.textContent = "Configured · identity not confirmed by contract v1";
  } else if (model?.name) {
    evidence.textContent = `${sourceLabel} identity · ${model.state}`;
  } else {
    evidence.textContent = "Identity not reported";
  }
  item.append(header, identity, evidence);
  return item;
}

function renderHandoffMap(run) {
  const map = byId("handoff-map");
  map.replaceChildren();

  const observed = document.createElement("section");
  observed.className = "handoff-domain";
  observed.setAttribute(
    "aria-label",
    traceSource === "synthetic_fixture"
      ? "Demo Ask Orchestrator actors"
      : "Observed Ask Orchestrator actors",
  );
  const observedLabel = document.createElement("p");
  observedLabel.className = "handoff-zone-label mb-3";
  observedLabel.textContent =
    traceSource === "synthetic_fixture"
      ? "Demo Ask Orchestrator boundary"
      : "Observed Ask Orchestrator boundary";
  const flow = document.createElement("ol");
  flow.className = "actor-flow";
  flow.append(
    actorNode(run, { label: "Router", order: 1, role: "router", stageId: "route" }),
    actorNode(run, {
      label: "Reviewer",
      order: 2,
      role: "reviewer",
      stageId: "review",
    }),
    actorNode(run, { label: "Judge", order: 3, role: "judge", stageId: "judge" }),
  );
  observed.append(observedLabel, flow);

  const connector = document.createElement("div");
  connector.className = "handoff-connector";
  connector.setAttribute("aria-label", "Advisory MCP result handed to external executor");
  const connectorLabel = document.createElement("span");
  connectorLabel.className = "small";
  connectorLabel.textContent = "MCP result";
  const connectorLine = document.createElement("span");
  connectorLine.className = "handoff-connector-line";
  connectorLine.setAttribute("aria-hidden", "true");
  const connectorDetail = document.createElement("span");
  connectorDetail.className = "small";
  connectorDetail.textContent = "advisory handoff";
  connector.append(connectorLabel, connectorLine, connectorDetail);

  const executor = document.createElement("section");
  executor.className = "handoff-executor";
  executor.setAttribute("aria-label", "Configured external executor");
  const executorLabel = document.createElement("p");
  executorLabel.className = "handoff-zone-label mb-3";
  executorLabel.textContent = "External execution boundary";
  const executorTitle = document.createElement("h3");
  executorTitle.className = "h6 mb-2";
  executorTitle.textContent = "Executor";
  const executorName = document.createElement("p");
  executorName.className = "executor-name mb-2";
  executorName.textContent = executorContext.name || "Not configured";
  const executorState = document.createElement("span");
  executorState.className = `state-pill state-${executorContext.state}`;
  executorState.textContent =
    executorContext.state === "configured" ? "configured · external" : "not reported";
  const executorPurpose = document.createElement("p");
  executorPurpose.className = "small text-secondary mt-3 mb-0";
  executorPurpose.textContent =
    "Receives the reviewed result and may apply code changes outside this observed trace.";
  executor.append(executorLabel, executorTitle, executorName, executorState, executorPurpose);

  map.append(observed, connector, executor);
  byId("executor-state").textContent = executorContext.name
    ? `${executorContext.name} · configured external`
    : "Executor not reported";
  byId("handoff-note").textContent =
    traceSource === "synthetic_fixture"
      ? "This is demo data: no models or executor ran. The dashed boundary shows where an external executor would receive the advisory result."
      : "The dashed boundary is deliberate: executor activity is not emitted by trace contract v1 and is never claimed as observed.";
}

function renderWorkflow(run) {
  const taskType = run.result?.task_type ? ` · ${run.result.task_type}` : "";
  byId("workflow-title").textContent = `Run ${run.run_id.slice(0, 8)}${taskType}`;
  const trail = byId("workflow-trail");
  trail.replaceChildren();
  const activeIndexes = run.workflow
    .map((stage, index) => ({ index, status: stage.status }))
    .filter((stage) => !["pending", "not_run"].includes(stage.status));
  const pendingIndex = run.workflow.findIndex((stage) => stage.status === "pending");
  const currentIndex = pendingIndex >= 0 ? pendingIndex : activeIndexes.at(-1)?.index;
  const maximumDuration = Math.max(
    ...run.workflow.map((stage) => stage.duration_ms || 0),
    1,
  );

  run.workflow.forEach((stage, index) => {
    const item = document.createElement("li");
    item.className = "workflow-stage";
    if (index === currentIndex && run.lifecycle === "in_progress") {
      item.classList.add("workflow-current");
      item.setAttribute("aria-current", "step");
    }
    item.setAttribute(
      "aria-label",
      `${stage.label}: ${stage.status.replace("_", " ")}`,
    );

    const header = document.createElement("div");
    header.className = "d-flex justify-content-between align-items-start gap-2";
    const label = document.createElement("h3");
    label.className = "h6 mb-0";
    label.textContent = stage.label;
    const status = statusPill(stage.status);
    header.append(label, status);

    const purpose = document.createElement("p");
    purpose.className = "small text-secondary mb-0";
    purpose.textContent = stage.purpose;

    const models = document.createElement("div");
    models.className = "workflow-models";
    const visibleStageModels = stage.models.filter((model) => visibleModelRoles.has(model.role));
    if (visibleStageModels.length) {
      visibleStageModels.forEach((model) => {
        const badge = document.createElement("span");
        badge.className = `model-attribution state-${model.state}`;
        badge.textContent = attributionLabel(model);
        models.append(badge);
        const configuredModel = configuredModelsByRole.get(model.role);
        if (configuredModel && configuredModel !== model.name) {
          const configured = document.createElement("span");
          configured.className = "model-attribution state-configured";
          configured.textContent = `${model.role}: ${configuredModel} · configured, not confirmed`;
          models.append(configured);
        }
      });
    } else {
      const control = document.createElement("span");
      control.className = "model-attribution model-control";
      control.textContent = stage.event_count
        ? `${stage.id === "retrieve" ? "retrieval operations" : "control flow"} · no model attribution`
        : "no model activity";
      models.append(control);
    }

    const metrics = document.createElement("p");
    metrics.className = "workflow-metrics small mb-0";
    const duration =
      stage.duration_ms === null ? "duration not reported" : formatDuration(stage.duration_ms);
    metrics.textContent = `${stage.event_count} events · ${duration}`;

    const durationVisual = document.createElement("div");
    durationVisual.className = "stage-duration";
    if (stage.duration_ms === null) {
      const unavailable = document.createElement("span");
      unavailable.className = "small text-secondary";
      unavailable.textContent = "No timing measurement";
      durationVisual.append(unavailable);
    } else {
      const progress = document.createElement("progress");
      progress.max = maximumDuration;
      progress.value = stage.duration_ms;
      progress.setAttribute("aria-label", `${stage.label} duration ${formatDuration(stage.duration_ms)}`);
      durationVisual.append(progress);
    }
    item.append(header, purpose, models, metrics, durationVisual);
    trail.append(item);
  });
}

function renderOverview(run) {
  const observedModels = run.models.filter(
    (model) => ["observed", "fallback"].includes(model.state) && model.name,
  );
  const runStatus = byId("run-status");
  runStatus.className = `trace-status trace-status-${run.status}`;
  runStatus.textContent = `${traceSource === "synthetic_fixture" ? "demo · " : ""}${run.lifecycle.replace("_", " ")} · ${run.status}`;
  byId("run-elapsed").textContent = formatDuration(run.summary.total_duration_ms);
  byId("run-window").textContent = run.summary.started_at
    ? `${new Date(run.summary.started_at).toLocaleString()}${run.summary.completed_at ? ` → ${new Date(run.summary.completed_at).toLocaleTimeString()}` : " → in progress"}`
    : "Start time not reported";
  byId("run-model-count").textContent = String(observedModels.length);
  byId("run-model-detail").textContent = observedModels.length
    ? observedModels.map((model) => `${model.role}: ${displayModelName(model)}`).join(" · ")
    : "No model identity observed";
  byId("run-provider-count").textContent = String(run.summary.provider_attempts);
  const providerNames = run.summary.providers.length
    ? run.summary.providers.join(" + ")
    : "provider not reported";
  byId("run-provider-detail").textContent = `${providerNames} · ${run.summary.provider_retries} retries${run.summary.fallback ? " · fallback" : ""}`;
  byId("run-task-type").textContent = run.result?.task_type || "Not reported";
  byId("run-result-status").textContent = run.result?.status || "No structured result yet";
}

function renderModelRoster(run) {
  const roster = byId("run-model-roster");
  roster.replaceChildren();
  run.models
    .filter((model) => visibleModelRoles.has(model.role))
    .forEach((model) => {
      const row = document.createElement("article");
    row.className = "model-roster-row";
    const identity = document.createElement("div");
    const role = document.createElement("h3");
    role.className = "h6 mb-1";
    role.textContent = formatRole(model.role);
    const name = document.createElement("p");
    name.className = "font-monospace small text-secondary mb-0";
    name.textContent = displayModelName(model);
    identity.append(role, name);
    const configuredModel = configuredModelsByRole.get(model.role);
    if (configuredModel && configuredModel !== model.name) {
      const configured = document.createElement("p");
      configured.className = "font-monospace small mb-0 mt-1";
      configured.textContent = `Configured: ${configuredModel}`;
      identity.append(configured);
    }
    const evidence = document.createElement("div");
    evidence.className = "model-roster-evidence";
    const state = document.createElement("span");
    state.className = `state-pill state-${model.state}`;
    state.textContent = model.state;
    evidence.append(state);
    if (configuredModel && model.state !== "configured") {
      const configuredState = document.createElement("span");
      configuredState.className = "state-pill state-configured";
      configuredState.textContent = "configured · not confirmed";
      evidence.append(configuredState);
    }
    const count = document.createElement("span");
    count.className = "small text-secondary";
    count.textContent = `${model.event_count} component events`;
    evidence.append(count);
    row.append(identity, evidence);
      roster.append(row);
    });
}

function resourceRow(label, value, detail) {
  const row = document.createElement("div");
  const term = document.createElement("dt");
  term.textContent = label;
  const description = document.createElement("dd");
  description.textContent = value;
  if (detail) {
    const note = document.createElement("span");
    note.className = "resource-detail";
    note.textContent = detail;
    description.append(note);
  }
  row.append(term, description);
  return row;
}

function renderResources(run) {
  const resources = byId("run-resources");
  resources.replaceChildren();
  const retrieval = run.summary.retrieval;
  const selected = retrieval.selected_count;
  const candidates = retrieval.candidate_count;
  const retrievalValue =
    selected !== null && candidates !== null
      ? `${selected} of ${candidates} selected`
      : selected !== null
        ? `${selected} selected`
        : candidates !== null
          ? `${candidates} candidates`
          : "Not reported";
  const useFlag = retrieval.context_used ?? retrieval.retrieval_used;
  resources.append(
    resourceRow(
      "Retrieval",
      retrievalValue,
      useFlag === null ? "Context use not reported" : useFlag ? "Context used" : "Context not used",
    ),
    resourceRow(
      "Embedding batch",
      run.summary.resources.embedding_batch_size === null
        ? "Not reported"
        : `${run.summary.resources.embedding_batch_size} items`,
      "Trace-reported batch size",
    ),
    resourceRow(
      "Reranking",
      run.summary.resources.rerank_selected_count === null
        ? "Not reported"
        : `${run.summary.resources.rerank_selected_count} selected`,
      run.summary.resources.rerank_candidate_count === null
        ? "Candidate count not reported"
        : `${run.summary.resources.rerank_candidate_count} candidates`,
    ),
    resourceRow(
      "Reported phase time",
      formatDuration(run.summary.reported_stage_duration_ms),
      `${run.summary.reported_stage_count} timed stages`,
    ),
    resourceRow("Tokens and cost", "Not reported", "Not available in contract v1"),
    resourceRow("CPU and memory", "Not reported", "Not available in contract v1"),
  );
}

function renderEvents(run) {
  renderOverview(run);
  renderHandoffMap(run);
  renderModelRoster(run);
  renderResources(run);
  renderWorkflow(run);
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
  byId("workflow-title").textContent = "No run selected";
  byId("run-status").className = "trace-status trace-status-pending";
  byId("run-status").textContent = "waiting";
  byId("run-elapsed").textContent = "—";
  byId("run-window").textContent = "No timing reported";
  byId("run-model-count").textContent = "—";
  byId("run-model-detail").textContent = "Awaiting attribution";
  byId("run-provider-count").textContent = "—";
  byId("run-provider-detail").textContent = "No attempts reported";
  byId("run-task-type").textContent = "—";
  byId("run-result-status").textContent = "No result reported";
  byId("executor-state").textContent = executorContext.name
    ? `${executorContext.name} · configured external`
    : "Executor not reported";
  const emptyHandoff = document.createElement("p");
  emptyHandoff.className = "text-secondary mb-0";
  emptyHandoff.textContent = "Waiting for an observed run before drawing actor handoffs…";
  byId("handoff-map").replaceChildren(emptyHandoff);
  const emptyRoster = document.createElement("p");
  emptyRoster.className = "text-secondary mb-0";
  emptyRoster.textContent = "Waiting for model attribution…";
  byId("run-model-roster").replaceChildren(emptyRoster);
  byId("run-resources").replaceChildren(
    resourceRow("Operational measurements", "Not reported", "Waiting for an observed run"),
  );
  const workflowEmpty = document.createElement("li");
  workflowEmpty.className = "text-secondary";
  workflowEmpty.textContent = "Waiting for an observed run…";
  byId("workflow-trail").replaceChildren(workflowEmpty);
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = 6;
  cell.className = "text-secondary";
  cell.textContent = "No observed runs are retained in this process.";
  row.append(cell);
  byId("trace-events").replaceChildren(row);
}

function renderRuns(payload) {
  traceSource = payload.source;
  byId("run-source-heading").textContent =
    payload.source === "synthetic_fixture" ? "DEMO WORKFLOW · NO LIVE MODELS" : "LATEST OBSERVED WORK";
  byId("models-engaged-label").textContent =
    payload.source === "synthetic_fixture" ? "DEMO MODEL ROLES" : "MODELS ENGAGED";
  byId("trace-source").textContent =
    payload.source === "synthetic_fixture"
      ? "Demo fixture · no live models"
      : payload.source === "sqlite_history"
        ? "SQLite history"
        : "Live process";
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
      summary.textContent = `${formatDuration(run.summary.total_duration_ms)} · ${run.event_count} events`;
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
    const modelRequest = fetchModelActivity().catch(() => null);
    const payload = await fetchRuns();
    const modelActivity = await modelRequest;
    if (modelActivity) {
      configuredModelsByRole = new Map(
        modelActivity.models
          .filter((model) => model.configured_model)
          .map((model) => [model.role, model.configured_model]),
      );
      executorContext = modelActivity.executor;
    }
    renderRuns(payload);
  } catch {
    byId("trace-refresh-status").textContent = "Updates unavailable; showing last known trace.";
  }
}

refreshRuns();
window.setInterval(refreshRuns, 2500);
