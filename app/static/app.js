"use strict";

const $ = (selector, parent = document) => parent.querySelector(selector);
const state = {
  q: "",
  status: "all",
  offset: 0,
  limit: 10,
  total: 0,
  items: [],
  selected: null,
  retryList: false,
};
const number = new Intl.NumberFormat();
const dayFormat = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
});
const dateTimeFormat = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});
const chartDayFormat = new Intl.DateTimeFormat("en", {
  weekday: "short",
  timeZone: "UTC",
});
let listRequest = 0;
let detailRequest = 0;
let searchTimer;
let toastTimer;
let lastDetailTrigger;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function actionButton(label, iconName, className = "icon-button") {
  const button = element("button", className);
  button.type = "button";
  button.setAttribute("aria-label", label);
  button.title = label;
  if (iconName) button.append(icon(iconName));
  return button;
}

function safeLink(url, text, className = "") {
  const link = element("a", className, text);
  const parsed = new URL(url, window.location.origin);
  if (["http:", "https:"].includes(parsed.protocol)) link.href = parsed.href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function formatDate(value, time = false) {
  return value
    ? (time ? dateTimeFormat : dayFormat).format(new Date(value))
    : "Not yet";
}

function displayUrl(url) {
  return url.replace(/^https?:\/\//, "").replace(/\/$/, "");
}

function linkName(link) {
  return link.title || new URL(link.url).hostname;
}

function statusBadge(status) {
  return element(
    "span",
    `status status-${status}`,
    status.charAt(0).toUpperCase() + status.slice(1),
  );
}

function showError(target, message) {
  target.textContent = message;
  target.hidden = !message;
}

function toast(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => {
    $("#toast").hidden = true;
  }, 3500);
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, {
      ...options,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
      cache: "no-store",
    });
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error(
        response.ok
          ? "The API returned an unexpected response. Please refresh and try again."
          : "The server couldn’t complete this request. Please try again shortly.",
      );
    }
    if (!response.ok) {
      const messages = Array.isArray(data.detail)
        ? data.detail
            .map(
              (item) =>
                `${item.loc?.at(-1) || "Request"}: ${item.msg.replace(/^Value error, /, "")}`,
            )
            .join(" ")
        : data.detail;
      throw new Error(
        messages || `Request failed (${response.status}). Please try again.`,
      );
    }
    $("#connection-state").textContent = "Connected to your API";
    $(".sidebar-footer").classList.add("connected");
    return data;
  } catch (error) {
    if (error.name === "AbortError" || error instanceof TypeError) {
      $("#connection-state").textContent = "Connection interrupted";
      $(".sidebar-footer").classList.remove("connected");
      throw new Error(
        "Couldn’t reach the API. Check that the server is running, then try again.",
      );
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

async function copyLink(url, button) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(url);
    } else {
      const field = element("textarea", "sr-only");
      field.value = url;
      document.body.append(field);
      field.select();
      const copied = document.execCommand("copy");
      field.remove();
      if (!copied) throw new Error("Clipboard unavailable");
    }
    toast("Short link copied. Ready to share.");
    if (button) {
      const originalLabel = button.getAttribute("aria-label");
      button.setAttribute("aria-label", "Copied");
      const use = $("use", button);
      if (use) use.setAttribute("href", "#i-check");
      setTimeout(() => {
        if (!button.isConnected) return;
        if (use) use.setAttribute("href", "#i-copy");
        if (originalLabel) button.setAttribute("aria-label", originalLabel);
        else button.removeAttribute("aria-label");
      }, 2000);
    }
  } catch {
    toast(
      "Copy isn’t available here. Select the short URL and copy it manually.",
    );
  }
}

function svgElement(tag, attributes = {}, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes))
    node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderChart(container, days) {
  container.replaceChildren();
  const total = days.reduce((sum, day) => sum + day.clicks, 0);
  const totalLabel = element("p", "chart-total");
  totalLabel.append(
    element("strong", "", number.format(total)),
    document.createTextNode(
      total === 1 ? "click this week" : "clicks this week",
    ),
  );
  container.append(totalLabel);
  const width = 360,
    height = 126,
    left = 29,
    bottom = 100,
    chartWidth = 320;
  const max = Math.max(4, ...days.map((day) => day.clicks));
  const svg = svgElement("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label":
      days.map((day) => `${day.date}: ${day.clicks} clicks`).join("; ") ||
      "No click history",
  });
  [0, 0.5, 1].forEach((fraction) => {
    const y = bottom - fraction * 86;
    svg.append(
      svgElement("line", {
        x1: left,
        y1: y,
        x2: width - 3,
        y2: y,
        stroke: "#e0e9e3",
        "stroke-dasharray": "3 4",
      }),
    );
    svg.append(
      svgElement(
        "text",
        {
          x: left - 8,
          y: y + 3,
          "text-anchor": "end",
          fill: "#65766b",
          "font-size": "9",
          "font-family": "Manrope, sans-serif",
        },
        number.format(Math.round(max * fraction)),
      ),
    );
  });
  days.forEach((day, index) => {
    const slot = chartWidth / Math.max(days.length, 1);
    const x = left + index * slot + slot * 0.25;
    const barHeight = Math.max(day.clicks ? 3 : 0, (day.clicks / max) * 86);
    const bar = svgElement("rect", {
      x,
      y: bottom - barHeight,
      width: slot * 0.5,
      height: barHeight,
      rx: "3",
      class: "chart-bar",
    });
    bar.append(
      svgElement(
        "title",
        {},
        `${day.date}: ${number.format(day.clicks)} clicks`,
      ),
    );
    svg.append(bar);
    const today = index === days.length - 1;
    svg.append(
      svgElement(
        "text",
        {
          x: x + slot * 0.25,
          y: 120,
          "text-anchor": "middle",
          fill: today ? "#176448" : "#65766b",
          "font-weight": today ? "700" : "500",
          "font-size": "9",
          "font-family": "Manrope, sans-serif",
        },
        today
          ? "Today"
          : chartDayFormat.format(new Date(`${day.date}T00:00:00Z`)),
      ),
    );
  });
  container.append(svg);
  if (!total)
    container.append(
      element("p", "chart-empty-note", "Your next click starts the story."),
    );
}

async function loadOverview() {
  try {
    const overview = await api("/links/overview");
    for (const [selector, key] of [
      ["#total-links", "total_links"],
      ["#total-clicks", "total_clicks"],
      ["#active-links", "active_links"],
      ["#clicks-today", "clicks_today"],
    ]) {
      $(selector).textContent = number.format(overview[key]);
    }
    renderChart($("#overview-chart"), overview.daily_clicks);
  } catch (error) {
    $("#overview-chart").replaceChildren(
      element(
        "p",
        "chart-loading",
        "Activity couldn’t load. Use Refresh to try again.",
      ),
    );
    throw error;
  }
}

function renderRows() {
  const tbody = $("#link-rows");
  tbody.replaceChildren();
  for (const link of state.items) {
    const row = element("tr", link.code === state.selected ? "selected" : "");
    const cell = element("td", "link-cell");
    const title = element("button", "link-title", linkName(link));
    title.type = "button";
    title.dataset.linkCode = link.code;
    title.dataset.linkAction = "title";
    title.title = `Details for ${linkName(link)}`;
    title.addEventListener("click", () => openDetails(link.code, title));
    const shortUrl = safeLink(
      link.short_url,
      displayUrl(link.short_url),
      "short-address",
    );
    shortUrl.title = "Open short link in a new tab (counts a click)";
    const destination = element("p", "destination-label", displayUrl(link.url));
    destination.title = link.url;
    cell.append(title, shortUrl, destination);
    const status = element("td");
    status.append(statusBadge(link.status));
    const clicks = element(
      "td",
      "click-column click-count",
      number.format(link.total_clicks),
    );
    clicks.append(
      element(
        "span",
        "mobile-click-label",
        link.total_clicks === 1 ? " click" : " clicks",
      ),
    );
    const created = element(
      "td",
      "created-column created-date",
      formatDate(link.created_at),
    );
    created.title = formatDate(link.created_at, true);
    const actions = element("td");
    const actionGroup = element("div", "row-actions");
    const copy = actionButton(`Copy ${link.code}`, "copy");
    copy.addEventListener("click", () => copyLink(link.short_url, copy));
    const detail = actionButton(
      `View ${link.code} details`,
      "chevron",
      "icon-button detail-action",
    );
    detail.dataset.linkCode = link.code;
    detail.dataset.linkAction = "details";
    detail.addEventListener("click", () => openDetails(link.code, detail));
    actionGroup.append(copy, detail);
    actions.append(actionGroup);
    row.append(cell, status, clicks, created, actions);
    tbody.append(row);
  }
  const empty = $("#empty-state");
  empty.hidden = state.items.length > 0;
  if (!state.items.length) {
    const filtered = Boolean(state.q || state.status !== "all");
    $("h3", empty).textContent = filtered
      ? "No links match this view"
      : "Your first short link starts here";
    $("p", empty).textContent = filtered
      ? "Try a different search or clear your filters to see all links."
      : "Paste a destination above. We’ll take care of the short version.";
    $("#empty-action").replaceChildren(
      document.createTextNode(filtered ? "Clear filters" : "Create a link"),
      icon("arrow"),
    );
  }
  $("#list-total").textContent = number.format(state.total);
  $("#page-range").textContent = state.total
    ? `${state.offset + 1}–${Math.min(state.offset + state.limit, state.total)} of ${number.format(state.total)} links`
    : "0 links";
  $("#previous-page").disabled = state.offset === 0;
  $("#next-page").disabled = state.offset + state.limit >= state.total;
}

async function loadLinks() {
  const request = ++listRequest;
  $("#list-loading").hidden = false;
  $("#empty-state").hidden = true;
  $("#link-rows").setAttribute("aria-busy", "true");
  try {
    const params = new URLSearchParams({
      q: state.q,
      status: state.status,
      limit: state.limit,
      offset: state.offset,
    });
    const result = await api(`/links?${params}`);
    if (request !== listRequest) return;
    state.items = result.items;
    state.total = result.total;
    state.retryList = false;
    if (state.offset >= state.total && state.offset > 0) {
      state.offset =
        Math.max(0, Math.ceil(state.total / state.limit) - 1) * state.limit;
      return await loadLinks();
    }
    renderRows();
  } catch (error) {
    if (request === listRequest && !state.items.length) {
      state.retryList = true;
      $("#empty-state").hidden = false;
      $("h3", $("#empty-state")).textContent = "Your links couldn’t load";
      $("p", $("#empty-state")).textContent =
        "Check your connection, then try again.";
      $("#empty-action").replaceChildren(
        document.createTextNode("Try again"),
        icon("refresh"),
      );
    }
    throw error;
  } finally {
    if (request === listRequest) {
      $("#list-loading").hidden = true;
      $("#link-rows").removeAttribute("aria-busy");
    }
  }
}

async function refresh({ details = false } = {}) {
  showError($("#page-error"), "");
  const button = $("#refresh-button");
  button.disabled = true;
  try {
    await Promise.all([loadOverview(), loadLinks()]);
    if (details && state.selected) await openDetails(state.selected);
  } catch (error) {
    showError($("#page-error"), error.message);
  } finally {
    button.disabled = false;
  }
}

function localDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

function formField(form, id, label, value, type = "text") {
  const field = element("div", "field");
  const labelNode = element("label", "", label);
  labelNode.htmlFor = id;
  const input = element("input");
  input.id = id;
  input.type = type;
  input.value = value ?? "";
  field.append(labelNode, input);
  form.append(field);
  return input;
}

function renderDetails(link) {
  const container = $("#detail-content");
  container.replaceChildren();
  container.append(
    element("h3", "detail-name", linkName(link)),
    statusBadge(link.status),
  );
  const address = element("div", "detail-address");
  const copy = actionButton(`Copy ${link.code}`, "copy");
  copy.addEventListener("click", () => copyLink(link.short_url, copy));
  address.append(safeLink(link.short_url, displayUrl(link.short_url)), copy);
  container.append(address);
  const stats = element("dl", "detail-stats");
  [
    ["Total clicks", number.format(link.total_clicks)],
    ["Last clicked", formatDate(link.last_clicked_at, true)],
    ["Created", formatDate(link.created_at, true)],
    ["Expires", link.expires_at ? formatDate(link.expires_at, true) : "Never"],
  ].forEach(([label, value], index) => {
    const group = element("div");
    group.append(
      element("dt", "", label),
      element("dd", index ? "small-value" : "", value),
    );
    stats.append(group);
  });
  container.append(
    stats,
    element("p", "detail-chart-heading", "Click history · last 7 days, UTC"),
  );
  const chart = element("div", "detail-chart");
  renderChart(chart, link.daily_clicks);
  container.append(chart);

  const form = element("form", "edit-form");
  form.append(element("h3", "", "Edit link"));
  const title = formField(form, "edit-title", "Label", link.title);
  title.maxLength = 120;
  const url = formField(form, "edit-url", "Destination URL", link.url, "url");
  url.required = true;
  url.maxLength = 2083;
  const expiry = formField(
    form,
    "edit-expiry",
    "Expires · your local time",
    localDateTime(link.expires_at),
    "datetime-local",
  );
  expiry.parentElement.append(
    element("small", "", "Leave blank for no expiry."),
  );
  const errorBox = element("div", "form-error");
  errorBox.setAttribute("role", "alert");
  errorBox.hidden = true;
  const save = element("button", "button button-primary", "Save changes");
  save.type = "submit";
  form.append(errorBox, save);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {};
    const newTitle = title.value.trim() || null;
    if (newTitle !== link.title) payload.title = newTitle;
    if (url.value.trim() !== link.url) payload.url = url.value.trim();
    if (expiry.value !== localDateTime(link.expires_at))
      payload.expires_at = expiry.value
        ? new Date(expiry.value).toISOString()
        : null;
    if (!Object.keys(payload).length)
      return toast("Everything is already up to date.");
    showError(errorBox, "");
    save.disabled = true;
    save.textContent = "Saving…";
    try {
      await api(`/links/${encodeURIComponent(link.code)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      toast("Link updated. The short address stays the same.");
      await refresh({ details: true });
    } catch (error) {
      showError(errorBox, error.message);
    } finally {
      save.disabled = false;
      save.textContent = "Save changes";
    }
  });
  container.append(form);

  const bottom = element("div", "detail-bottom");
  const toggle = element("button", "button button-quiet");
  toggle.type = "button";
  toggle.append(
    icon(link.is_active ? "pause" : "play"),
    document.createTextNode(link.is_active ? "Pause link" : "Resume link"),
  );
  const toggleError = element("div", "form-error");
  toggleError.setAttribute("role", "alert");
  toggleError.hidden = true;
  toggle.addEventListener("click", async () => {
    toggle.disabled = true;
    showError(toggleError, "");
    try {
      await api(`/links/${encodeURIComponent(link.code)}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: !link.is_active }),
      });
      toast(
        link.is_active
          ? "Link paused. Its history is safe."
          : link.is_expired
            ? "Link resumed, but still expired. Update its expiry to enable redirects."
            : "Link resumed. Ready for visitors.",
      );
      await refresh({ details: true });
    } catch (error) {
      showError(toggleError, error.message);
    } finally {
      toggle.disabled = false;
    }
  });
  bottom.append(
    toggle,
    element(
      "p",
      "",
      link.is_active
        ? "Pausing stops redirects and keeps all your click history."
        : link.is_expired
          ? "This link has also expired. Change or clear its expiry before sharing again."
          : "Resume whenever you’re ready. Your expiry setting stays unchanged.",
    ),
    toggleError,
  );
  container.append(bottom);
}

async function openDetails(code, trigger) {
  const request = ++detailRequest;
  state.selected = code;
  if (trigger) lastDetailTrigger = { code, action: trigger.dataset.linkAction };
  $("#detail-panel").hidden = false;
  $("#ledger-layout").classList.add("has-detail");
  $("#detail-content").replaceChildren(
    element("p", "chart-loading", "Loading link details…"),
  );
  renderRows();
  try {
    const link = await api(`/links/${encodeURIComponent(code)}/stats`);
    if (request !== detailRequest) return;
    renderDetails(link);
    if (trigger) {
      $("#detail-panel").focus({ preventScroll: true });
      if (window.matchMedia("(max-width: 1000px)").matches)
        $("#detail-panel").scrollIntoView({
          block: "start",
          behavior: window.matchMedia("(prefers-reduced-motion: reduce)")
            .matches
            ? "auto"
            : "smooth",
        });
    }
  } catch (error) {
    if (request !== detailRequest) return;
    const message = element("p", "form-error", error.message);
    message.setAttribute("role", "alert");
    $("#detail-content").replaceChildren(message);
  }
}

function closeDetails() {
  detailRequest++;
  state.selected = null;
  $("#detail-panel").hidden = true;
  $("#ledger-layout").classList.remove("has-detail");
  renderRows();
  const trigger = [...document.querySelectorAll("[data-link-code]")].find(
    (button) =>
      button.dataset.linkCode === lastDetailTrigger?.code &&
      button.dataset.linkAction === lastDetailTrigger?.action,
  );
  if (trigger) trigger.focus({ preventScroll: true });
  else $("#search").focus({ preventScroll: true });
}

$("#create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError($("#create-error"), "");
  const payload = { url: $("#destination").value.trim() };
  if ($("#alias").value.trim()) payload.alias = $("#alias").value.trim();
  if ($("#link-title").value.trim())
    payload.title = $("#link-title").value.trim();
  const expiryChoice = $("#expiry").value;
  if (expiryChoice === "custom") {
    if (!$("#custom-expiry").value)
      return showError(
        $("#create-error"),
        "Choose an expiry date and time, or select Never.",
      );
    payload.expires_at = new Date($("#custom-expiry").value).toISOString();
  } else if (expiryChoice !== "never")
    payload.expires_at = new Date(
      Date.now() + Number(expiryChoice) * 3600000,
    ).toISOString();
  const button = $("#create-button");
  button.disabled = true;
  button.textContent = "Creating…";
  try {
    const link = await api("/links", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("#created-url").textContent = link.short_url;
    $("#created-url").href = link.short_url;
    $("#created-result").hidden = false;
    $("#copy-created").onclick = () =>
      copyLink(link.short_url, $("#copy-created"));
    $("#create-form").reset();
    $("#custom-expiry-field").hidden = true;
    $("#custom-expiry").required = false;
    state.offset = 0;
    state.q = "";
    state.status = "all";
    $("#search").value = "";
    $("#status-filter").value = "all";
    toast("Your short link is ready.");
    await refresh();
    $("#copy-created").focus({ preventScroll: true });
  } catch (error) {
    showError($("#create-error"), error.message);
  } finally {
    button.disabled = false;
    button.replaceChildren(
      document.createTextNode("Shorten link"),
      icon("arrow"),
    );
  }
});

$("#expiry").addEventListener("change", () => {
  const custom = $("#expiry").value === "custom";
  $("#custom-expiry-field").hidden = !custom;
  $("#custom-expiry").required = custom;
  if (custom) $("#custom-expiry").focus();
});
$("#search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.q = $("#search").value.trim();
    state.offset = 0;
    loadLinks().catch((error) => showError($("#page-error"), error.message));
  }, 250);
});
$("#status-filter").addEventListener("change", () => {
  state.status = $("#status-filter").value;
  state.offset = 0;
  loadLinks().catch((error) => showError($("#page-error"), error.message));
});
$("#previous-page").addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.limit);
  loadLinks().catch((error) => showError($("#page-error"), error.message));
});
$("#next-page").addEventListener("click", () => {
  state.offset += state.limit;
  loadLinks().catch((error) => showError($("#page-error"), error.message));
});
$("#empty-action").addEventListener("click", () => {
  if (state.retryList) return refresh();
  if (state.q || state.status !== "all") {
    state.q = "";
    state.status = "all";
    state.offset = 0;
    $("#search").value = "";
    $("#status-filter").value = "all";
    loadLinks().catch((error) => showError($("#page-error"), error.message));
  } else $("#destination").focus();
});
$("#refresh-button").addEventListener("click", () =>
  refresh({ details: true }),
);
$("#close-detail").addEventListener("click", closeDetails);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.selected) closeDetails();
});
document.querySelectorAll('.nav-item[href^="#"]').forEach((link) =>
  link.addEventListener("click", () => {
    document
      .querySelectorAll('.nav-item[href^="#"]')
      .forEach((item) => item.classList.toggle("current", item === link));
  }),
);
refresh();
