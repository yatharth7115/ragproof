const summaryElement = document.querySelector("#summary");
const rowsElement = document.querySelector("#incident-rows");
const resultCount = document.querySelector("#result-count");
const statusFilter = document.querySelector("#status-filter");
const categoryFilter = document.querySelector("#category-filter");
const errorState = document.querySelector("#error-state");
const emptyState = document.querySelector("#empty-state");
const drawer = document.querySelector("#incident-drawer");
const drawerBackdrop = document.querySelector("#drawer-backdrop");
const drawerContent = document.querySelector("#drawer-content");
const postureDial = document.querySelector("#posture-dial");
const postureNumber = document.querySelector("#posture-number");
const postureHeading = document.querySelector("#posture-heading");
const postureNote = document.querySelector("#posture-note");
const refreshButton = document.querySelector("#refresh-button");
const connectionState = document.querySelector("#connection-state");
let pageOffset = 0;
const pageSize = 15;
let listRequest = 0;
let detailRequest = 0;
let returnFocus = null;
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const statusLabel = (status) => status.replaceAll("_", " ");
const shortId = (value) => value ? `${value.slice(0, 9)}…${value.slice(-5)}` : "—";
const dateLabel = (value) => new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium", timeStyle: "short"
}).format(new Date(value));

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function badge(status) {
  return element("span", `badge badge-${status.toLowerCase()}`, statusLabel(status));
}

function renderSummary(summary) {
  const statuses = summary.status_counts;
  const attention = statuses.suspected + statuses.replaying + statuses.needs_review;
  const confirmedRate = summary.total ? Math.round((statuses.confirmed / summary.total) * 100) : 0;
  const metrics = [
    ["All incidents", summary.total, "Recorded diagnoses", "var(--accent)"],
    ["Confirmed", statuses.confirmed, "Replay-proven", "var(--green)"],
    ["Awaiting review", attention, "Replay or review pending", "var(--amber)"],
    ["Rejected", statuses.rejected, "Change did not fix failure", "var(--orange)"],
  ];

  postureDial.style.setProperty("--posture", `${confirmedRate * 3.6}deg`);
  postureNumber.textContent = `${confirmedRate}%`;
  if (!summary.total) {
    postureHeading.textContent = "Your evidence starts here";
    postureNote.textContent = "No diagnoses have been recorded yet.";
    postureNumber.textContent = "—";
  } else if (attention === 0) {
    postureHeading.textContent = "Ledger resolved";
    postureNote.textContent = "No diagnosis is awaiting replay or review.";
  } else {
    postureHeading.textContent = `${attention} case${attention === 1 ? "" : "s"} need a decision`;
    postureNote.textContent = `${confirmedRate}% of recorded diagnoses are confirmed by controlled replay.`;
  }

  summaryElement.replaceChildren(...metrics.map(([label, value, context, color]) => {
    const cell = element("article", "signal-cell");
    cell.style.setProperty("--cell-color", color);
    cell.append(
      element("span", "signal-label", label),
      element("span", "signal-value", String(value)),
      element("span", "signal-context", context),
    );
    cell.addEventListener("pointermove", (event) => {
      if (reducedMotion.matches || event.pointerType !== "mouse") return;
      const rect = cell.getBoundingClientRect();
      cell.style.setProperty("--mx", `${event.clientX - rect.left}px`);
      cell.style.setProperty("--my", `${event.clientY - rect.top}px`);
    });
    return cell;
  }));
}

function renderCategories(categoryCounts) {
  const selected = categoryFilter.value;
  const first = element("option", "", "All categories");
  first.value = "";
  const options = Object.keys(categoryCounts).map((category) => {
    const option = element("option", "", `${category.replaceAll("_", " ")} · ${categoryCounts[category]}`);
    option.value = category;
    return option;
  });
  categoryFilter.replaceChildren(first, ...options);
  categoryFilter.value = selected;
}

function labelledCell(label, value, className = "") {
  const wrapper = element("span", className);
  wrapper.append(element("span", "cell-label", `${label}:`));
  wrapper.append(document.createTextNode(value));
  return wrapper;
}

function renderRows(payload) {
  rowsElement.replaceChildren();
  payload.incidents.forEach((incident, index) => {
    const row = element("button", `case-row status-${incident.status}`);
    row.type = "button";
    row.dataset.index = String(payload.page.offset + index + 1).padStart(3, "0");
    row.setAttribute("aria-label", `Open ${incident.category} incident`);

    const identity = element("div", "case-identity");
    identity.append(
      element("strong", "", statusLabel(incident.category.toLowerCase())),
      element("span", "mono", shortId(incident.diagnosis_id)),
    );
    const state = document.createElement("span");
    state.append(badge(incident.status));
    const component = labelledCell("Component", incident.component);
    const claims = labelledCell("Claims", String(incident.claim_count));
    const replay = labelledCell("Replay", incident.replay ? incident.replay.outcome : "Not run");
    if (incident.regression_case_id) {
      replay.append(element("span", "regression-stamp", "Regression saved"));
    }
    const recorded = labelledCell("Recorded", dateLabel(incident.created_at));
    row.append(identity, state, component, claims, replay, recorded);
    row.addEventListener("click", () => openIncident(incident.diagnosis_id));
    rowsElement.append(row);
  });
  resultCount.textContent = `${payload.page.total} recorded ${payload.page.total === 1 ? "diagnosis" : "diagnoses"} in this view`;
  document.querySelector("#previous-page").disabled = pageOffset === 0;
  document.querySelector("#next-page").disabled = pageOffset + pageSize >= payload.page.total;
  document.querySelector("#page-label").textContent = payload.page.total
    ? `${pageOffset + 1}–${pageOffset + payload.incidents.length} of ${payload.page.total}`
    : "0 results";
  emptyState.hidden = payload.incidents.length !== 0;
}

function detailField(label, value, mono = false) {
  const wrapper = element("div", "detail-field");
  wrapper.append(element("dt", "", label), element("dd", mono ? "mono" : "", value ?? "—"));
  return wrapper;
}

function renderIncident(detail) {
  const diagnosis = detail.diagnosis;
  drawerContent.replaceChildren();

  const overview = element("section", "detail-section");
  const overviewGrid = element("dl", "detail-grid");
  overviewGrid.append(
    detailField("State", statusLabel(diagnosis.status)),
    detailField("Failure mode", diagnosis.primary_hypothesis.category.replaceAll("_", " ")),
    detailField("Component", diagnosis.primary_hypothesis.component),
    detailField("Confidence", `${Math.round(diagnosis.primary_hypothesis.confidence * 100)}%`),
    detailField("Trace", diagnosis.trace_id, true),
    detailField("Diagnosis", diagnosis.diagnosis_id, true),
  );
  overview.append(element("h3", "", "01 / Diagnosis"), overviewGrid);

  const verification = element("section", "detail-section");
  const verdictList = element("div", "verdict-list");
  const claims = detail.verification?.claims || [];
  if (claims.length) {
    claims.forEach((claim) => verdictList.append(badge(claim.verdict)));
  } else {
    verdictList.append(element("span", "mono", detail.verification?.blocked_reason || "No claim verdict recorded"));
  }
  verification.append(element("h3", "", "02 / Claim verdicts"), verdictList);

  const replay = element("section", "detail-section");
  replay.append(element("h3", "", "03 / Controlled replay"));
  if (detail.replay) {
    const grid = element("dl", "detail-grid");
    grid.append(
      detailField("Outcome", detail.replay.outcome),
      detailField("Changed", detail.replay.changed_variable.component),
      detailField("From", detail.replay.changed_variable.from_value),
      detailField("To", detail.replay.changed_variable.to_value),
      detailField("Replay", detail.replay.replay_id, true),
      detailField("Regression", detail.regression_case ? "Saved" : "Not promoted"),
    );
    replay.append(grid);
  } else {
    replay.append(element("p", "mono", "No controlled replay recorded."));
  }

  const lineage = element("section", "detail-section");
  lineage.append(element("h3", "", "04 / Evidence lineage"));
  if (detail.lineage) {
    const contextCount = detail.lineage.candidates.filter((item) => item.included_in_context).length;
    const grid = element("dl", "detail-grid");
    grid.append(
      detailField("Retrieved", String(detail.lineage.candidates.length)),
      detailField("Used in context", String(contextCount)),
      detailField("Citations", String(detail.lineage.citations.length)),
      detailField("Corpus", detail.lineage.corpus_version),
      detailField("Query hash", detail.lineage.query_hash, true),
      detailField("Response hash", detail.lineage.response_hash, true),
    );
    lineage.append(grid);
  }

  const timelineSection = element("section", "detail-section");
  const timeline = element("ol", "timeline");
  detail.timeline.forEach((entry, index) => {
    const item = document.createElement("li");
    item.dataset.step = String(index + 1).padStart(2, "0");
    item.append(element("strong", "", statusLabel(entry.status).toUpperCase()), element("span", "mono", dateLabel(entry.changed_at)));
    timeline.append(item);
  });
  timelineSection.append(element("h3", "", "05 / Lifecycle"), timeline);
  drawerContent.append(overview, verification, replay, lineage, timelineSection);
}

async function loadIncidents() {
  const request = ++listRequest;
  errorState.hidden = true;
  emptyState.hidden = true;
  rowsElement.setAttribute("aria-busy", "true");
  refreshButton.disabled = true;
  connectionState.textContent = "Refreshing";
  const query = new URLSearchParams();
  query.set("limit", String(pageSize));
  query.set("offset", String(pageOffset));
  if (statusFilter.value) query.set("status", statusFilter.value);
  if (categoryFilter.value) query.set("category", categoryFilter.value);
  try {
    const response = await fetch(`/v1/incidents?${query}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (request !== listRequest) return;
    renderSummary(payload.summary);
    renderCategories(payload.summary.category_counts);
    renderRows(payload);
    connectionState.textContent = "Connected";
    connectionState.classList.add("connected");
  } catch (error) {
    if (request !== listRequest) return;
    console.error(error);
    rowsElement.replaceChildren();
    resultCount.textContent = "Ledger unavailable";
    errorState.hidden = false;
    summaryElement.replaceChildren(element("p", "message-state", "Summary unavailable"));
    postureNumber.textContent = "—";
    postureDial.style.setProperty("--posture", "0deg");
    postureHeading.textContent = "Connection interrupted";
    postureNote.textContent = "Refresh to reconnect to your recorded evidence.";
    connectionState.textContent = "Offline";
    connectionState.classList.remove("connected");
    document.querySelector("#previous-page").disabled = true;
    document.querySelector("#next-page").disabled = true;
    document.querySelector("#page-label").textContent = "—";
  } finally {
    if (request === listRequest) {
      rowsElement.setAttribute("aria-busy", "false");
      refreshButton.disabled = false;
    }
  }
}

async function openIncident(diagnosisId) {
  const request = ++detailRequest;
  returnFocus = document.activeElement;
  drawerBackdrop.hidden = false;
  drawer.inert = false;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  document.querySelector(".app").inert = true;
  document.querySelector(".sidebar").inert = true;
  document.body.style.overflow = "hidden";
  requestAnimationFrame(() => {
    if (request === detailRequest) document.querySelector("#drawer-close").focus();
  });
  drawerContent.replaceChildren(element("p", "mono", "READING EVIDENCE RECORD…"));
  try {
    const response = await fetch(`/v1/incidents/${encodeURIComponent(diagnosisId)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const detail = await response.json();
    if (request !== detailRequest) return;
    renderIncident(detail);
  } catch (error) {
    if (request !== detailRequest) return;
    console.error(error);
    drawerContent.replaceChildren(element("p", "mono", "INCIDENT EVIDENCE COULD NOT BE LOADED."));
  }
}

function closeDrawer() {
  ++detailRequest;
  drawer.inert = true;
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  document.querySelector(".app").inert = false;
  document.querySelector(".sidebar").inert = false;
  document.body.style.overflow = "";
  drawerBackdrop.hidden = true;
  if (returnFocus?.isConnected) returnFocus.focus();
}

document.querySelector("#refresh-button").addEventListener("click", loadIncidents);
document.querySelector("#drawer-close").addEventListener("click", closeDrawer);
drawerBackdrop.addEventListener("click", closeDrawer);
function resetAndLoad() { pageOffset = 0; loadIncidents(); }
statusFilter.addEventListener("change", resetAndLoad);
categoryFilter.addEventListener("change", resetAndLoad);
document.querySelector("#previous-page").addEventListener("click", () => {
  pageOffset = Math.max(0, pageOffset - pageSize); loadIncidents();
});
document.querySelector("#next-page").addEventListener("click", () => {
  pageOffset += pageSize; loadIncidents();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && drawer.classList.contains("open")) closeDrawer();
  if (event.key === "Tab" && drawer.classList.contains("open")) {
    const focusable = [...drawer.querySelectorAll('button:not(:disabled), a[href], select, [tabindex="0"]')];
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});

loadIncidents();
