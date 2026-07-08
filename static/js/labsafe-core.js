(function () {
  const riskMeta = {
    normal: { label: "正常", className: "risk-normal", tone: "normal" },
    notice: { label: "注意", className: "risk-notice", tone: "notice" },
    warning: { label: "预警", className: "risk-warning", tone: "warning" },
    danger: { label: "危险", className: "risk-danger", tone: "danger" }
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function markdownLite(text) {
    return escapeHtml(text || "")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\n/g, "<br>");
  }

  async function api(path, options = {}) {
    const { timeoutMs = 8000, ...fetchOptions } = options;
    const controller = timeoutMs ? new AbortController() : null;
    const timer = timeoutMs ? window.setTimeout(() => controller.abort(), timeoutMs) : null;
    const init = {
      cache: "no-store",
      ...fetchOptions,
      signal: controller?.signal,
      headers: {
        ...(fetchOptions.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...(fetchOptions.headers || {})
      }
    };
    try {
      const response = await fetch(path, init);
      const text = await response.text();
      let data;
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        data = { success: false, error: text || response.statusText };
      }
      if (!response.ok) {
        throw new Error(data.error || response.statusText || `HTTP ${response.status}`);
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error(`请求超时: ${path}`);
      throw error;
    } finally {
      if (timer) window.clearTimeout(timer);
    }
  }

  function post(path, body = {}, options = {}) {
    return api(path, { method: "POST", body: JSON.stringify(body), ...options });
  }

  function setText(id, value, fallback = "--") {
    const node = typeof id === "string" ? document.getElementById(id) : id;
    if (node) node.textContent = value ?? fallback;
  }

  function formatNumber(value, digits = 1, suffix = "") {
    if (value === null || value === undefined || value === "") return "--";
    const num = Number(value);
    if (!Number.isFinite(num)) return String(value);
    return `${num.toFixed(digits)}${suffix}`;
  }

  function formatTime(value) {
    if (!value) return "--";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString();
  }

  function formatAgentState(agentData = {}) {
    if (agentData.enabled === false) return "停用";
    const raw = String(agentData.agent_service || agentData.status || "").toLowerCase();
    if (!raw || raw === "embedded_fallback" || raw === "fallback") return "可用";
    if (raw.includes("error") || raw.includes("unavailable")) return "不可用";
    return raw.replaceAll("_", " ");
  }

  function makeBadge(level, label) {
    const normalized = String(level || "normal").toLowerCase();
    const meta = riskMeta[normalized] || riskMeta.normal;
    return `<span class="risk-badge ${meta.className}">${escapeHtml(label || meta.label)}</span>`;
  }

  function toast(message, type = "info") {
    let root = document.getElementById("toastRoot");
    if (!root) {
      root = document.createElement("div");
      root.id = "toastRoot";
      root.className = "toast-root";
      document.body.appendChild(root);
    }
    const node = document.createElement("div");
    node.className = `toast ${type === "error" ? "error" : ""}`;
    node.textContent = message;
    root.appendChild(node);
    window.setTimeout(() => node.remove(), 3600);
  }

  function openModal({ title, body, danger = false, confirmText = "确认", cancelText = "取消", onConfirm }) {
    const backdrop = document.getElementById("globalModal");
    if (!backdrop) return;
    $(".modal-title", backdrop).textContent = title || "确认操作";
    $(".modal-body", backdrop).innerHTML = body || "";
    const confirm = $(".modal-confirm", backdrop);
    const cancel = $(".modal-cancel", backdrop);
    confirm.textContent = confirmText;
    cancel.textContent = cancelText;
    confirm.className = `btn modal-confirm ${danger ? "btn-danger" : "btn-primary"}`;
    backdrop.classList.add("open");
    const cleanup = () => {
      backdrop.classList.remove("open");
      confirm.onclick = null;
      cancel.onclick = null;
    };
    cancel.onclick = cleanup;
    confirm.onclick = async () => {
      try {
        await onConfirm?.();
        cleanup();
      } catch (error) {
        toast(error.message || "操作失败", "error");
      }
    };
  }

  function applyRiskNodes(level, reason) {
    const normalized = String(level || "normal").toLowerCase();
    const meta = riskMeta[normalized] || riskMeta.normal;
    const badgeHtml = makeBadge(normalized, meta.label);
    $$(".js-risk-badge").forEach((node) => {
      node.innerHTML = badgeHtml;
    });
    setText("topRiskText", meta.label);
    const dangerAlert = document.getElementById("dangerAlert");
    if (dangerAlert) {
      dangerAlert.classList.toggle("open", normalized === "danger");
      dangerAlert.textContent = reason || "检测到高风险状态，请立即检查实验室现场。";
    }
  }

  async function refreshShellStatus() {
    try {
      const [status, agent, emergency] = await Promise.allSettled([
        api("/api/status"),
        api("/api/agent/status"),
        api("/api/emergency-call/status")
      ]);
      const statusData = status.status === "fulfilled" ? status.value : {};
      const agentData = agent.status === "fulfilled" ? agent.value : {};
      const emergencyData = emergency.status === "fulfilled" ? emergency.value : {};
      const risk = agentData.risk_level || statusData.alert_level || "normal";
      const reason = agentData.reason || statusData.fire_state?.alarm_reason || "";
      applyRiskNodes(risk, reason);
      setText("topStatusText", [
        statusData.status === "running" ? "服务运行中" : "服务待确认",
        statusData.fire_state?.alarm_active ? "火警触发" : "火警正常",
        `助手${formatAgentState(agentData)}`,
        emergencyData.ready ? "应急电话就绪" : "应急电话待确认"
      ].join(" · "));
      setText("inspectorTemperature", formatNumber(statusData.fire_state?.temperature, 1, "°C"));
      setText("inspectorHumidity", formatNumber(statusData.fire_state?.humidity, 1, "%"));
      setText("inspectorFire", statusData.fire_state?.alarm_active ? "active" : "clear");
      setText("inspectorAgent", formatAgentState(agentData));
      setText("inspectorModel", agentData.models?.active_model || agentData.model?.model || "--");
      setText("inspectorEmergency", emergencyData.ready ? "ready" : (emergencyData.message || "check"));
      setText("inspectorReason", reason || "暂无异常原因");
      window.dispatchEvent(new CustomEvent("labsafe:shell-status", {
        detail: { status: statusData, agent: agentData, emergency: emergencyData }
      }));
    } catch (error) {
      setText("topStatusText", "status unavailable");
    }
  }

  function startClock() {
    const tick = () => setText("topTime", new Date().toLocaleTimeString());
    tick();
    window.setInterval(tick, 1000);
  }

  function initShell() {
    startClock();
    refreshShellStatus();
    window.setInterval(refreshShellStatus, 5000);
  }

  function drawDetections(img, overlay, data) {
    if (!img || !overlay) return;
    overlay.innerHTML = "";
    const detections = data?.detections || [];
    if (!detections.length || !img.naturalWidth || !img.naturalHeight) return;
    const rect = img.getBoundingClientRect();
    const frame = img.closest(".camera-frame")?.getBoundingClientRect() || rect;
    const imageRatio = img.naturalWidth / img.naturalHeight;
    const rectRatio = rect.width / rect.height;
    let shownWidth = rect.width;
    let shownHeight = rect.height;
    let offsetX = rect.left - frame.left;
    let offsetY = rect.top - frame.top;
    if (rectRatio > imageRatio) {
      shownWidth = rect.height * imageRatio;
      offsetX += (rect.width - shownWidth) / 2;
    } else {
      shownHeight = rect.width / imageRatio;
      offsetY += (rect.height - shownHeight) / 2;
    }
    detections.forEach((det) => {
      const b = det.bbox || [];
      if (b.length < 4) return;
      const [x1, y1, x2, y2] = b.map(Number);
      const box = document.createElement("div");
      box.className = "detection-box";
      box.style.left = `${offsetX + x1 / img.naturalWidth * shownWidth}px`;
      box.style.top = `${offsetY + y1 / img.naturalHeight * shownHeight}px`;
      box.style.width = `${Math.max(0, (x2 - x1) / img.naturalWidth * shownWidth)}px`;
      box.style.height = `${Math.max(0, (y2 - y1) / img.naturalHeight * shownHeight)}px`;
      const label = document.createElement("div");
      label.className = "detection-label";
      label.textContent = `${det.class_name || "object"} ${Math.round((det.confidence || 0) * 100)}%`;
      box.appendChild(label);
      overlay.appendChild(box);
    });
  }

  function renderLogLine(item) {
    const level = item.level || item.risk_level || item.type || "info";
    const time = item.time || item.timestamp || item.created_at || "";
    const message = item.message || item.content || item.reason || item.title || JSON.stringify(item);
    return `<div class="log-line ${escapeHtml(level)}">
      <span>${escapeHtml(formatTime(time))}</span>
      <span>${escapeHtml(String(level).toUpperCase())}</span>
      <span>${markdownLite(message)}</span>
    </div>`;
  }

  function renderKeyValue(root, rows) {
    const node = typeof root === "string" ? document.getElementById(root) : root;
    if (!node) return;
    node.innerHTML = rows.map(([key, value, mono]) => `
      <div style="display:flex;justify-content:space-between;gap:12px;padding:7px 0;border-bottom:1px solid var(--border-subtle);">
        <span class="muted">${escapeHtml(key)}</span>
        <span class="${mono ? "mono" : ""}" style="text-align:right;overflow-wrap:anywhere;">${escapeHtml(value ?? "--")}</span>
      </div>
    `).join("");
  }

  window.LabSafe = {
    $, $$, api, post, toast, openModal, escapeHtml, markdownLite, makeBadge,
    setText, formatNumber, formatTime, formatAgentState, initShell, refreshShellStatus,
    drawDetections, renderLogLine, renderKeyValue, riskMeta
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initShell, { once: true });
  } else {
    initShell();
  }
})();
