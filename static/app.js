const SHEET_COLS = 10;
const SHEET_ROWS = 20;
const SHEET_CAPACITY = SHEET_COLS * SHEET_ROWS;
const DEFAULT_BRAND = "sexybam";

/** @type {string} */
let activeBrand = DEFAULT_BRAND;
/** @type {Array<{id:string,label:string,brand_text:string}>} */
let brands = [];

/** @type {Map<string, object>} */
const labels = new Map();
/** @type {Set<string>} */
const selected = new Set();
/** @type {Array<object>} */
let catalog = [];
/** @type {Set<string>} */
const catalogSelected = new Set();

/** @type {Map<string, Set<string>>} */
const selectedByBrand = new Map();
/** @type {Map<string, Set<string>>} */
const catalogSelectedByBrand = new Map();

const sheetGrid = document.getElementById("sheetGrid");
const sheetMeta = document.getElementById("sheetMeta");
const selectedCount = document.getElementById("selectedCount");
const exportBtn = document.getElementById("exportBtn");
const formError = document.getElementById("formError");
const toast = document.getElementById("toast");
const catalogList = document.getElementById("catalogList");
const catalogMeta = document.getElementById("catalogMeta");
const importCatalogBtn = document.getElementById("importCatalogBtn");
const brandTabs = document.getElementById("brandTabs");
const catalogPanelTitle = document.getElementById("catalogPanelTitle");
const sheetPanelTitle = document.getElementById("sheetPanelTitle");
const previewModal = document.getElementById("previewModal");
const previewModalTitle = document.getElementById("previewModalTitle");
const previewModalMeta = document.getElementById("previewModalMeta");
const previewModalImage = document.getElementById("previewModalImage");
const previewModalBody = previewModal.querySelector(".preview-modal-body");

function brandQuery(extra = "") {
  const q = `brand=${encodeURIComponent(activeBrand)}`;
  return extra ? `${extra}${extra.includes("?") ? "&" : "?"}${q}` : `?${q}`;
}

function currentBrandInfo() {
  return brands.find((b) => b.id === activeBrand) || { id: activeBrand, label: activeBrand, brand_text: "" };
}

function saveBrandUiState() {
  selectedByBrand.set(activeBrand, new Set(selected));
  catalogSelectedByBrand.set(activeBrand, new Set(catalogSelected));
}

function restoreBrandUiState() {
  selected.clear();
  catalogSelected.clear();
  const sel = selectedByBrand.get(activeBrand);
  const cat = catalogSelectedByBrand.get(activeBrand);
  if (sel) sel.forEach((id) => selected.add(id));
  if (cat) cat.forEach((code) => catalogSelected.add(code));
}

function renderBrandTabs() {
  brandTabs.innerHTML = "";
  brands.forEach((b) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "brand-tab" + (b.id === activeBrand ? " is-active" : "");
    btn.textContent = b.label;
    btn.dataset.brand = b.id;
    btn.addEventListener("click", () => switchBrand(b.id));
    brandTabs.appendChild(btn);
  });
  const info = currentBrandInfo();
  catalogPanelTitle.textContent = `AI 파일 항목 (${info.label})`;
  sheetPanelTitle.textContent = `시트 미리보기 · ${info.label}`;
}

async function switchBrand(brandId) {
  if (brandId === activeBrand) return;
  saveBrandUiState();
  activeBrand = brandId;
  restoreBrandUiState();
  renderBrandTabs();
  closePreviewModal();
  await Promise.all([loadLabels(), loadCatalog()]);
}

function openPreviewModal(item) {
  previewModal.hidden = false;
  previewModal.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  previewModalTitle.textContent = `${item.code} ${item.name}`;
  previewModalMeta.textContent = `EAN-13: ${item.barcode} · ${item.row + 1}행 ${item.col + 1}열 · ${currentBrandInfo().label}`;
  previewModalBody.classList.add("is-loading");
  previewModalImage.src = `/api/labels/${item.id}/preview?size=large&brand=${activeBrand}&t=${Date.now()}`;
  previewModalImage.onload = () => previewModalBody.classList.remove("is-loading");
}

function closePreviewModal() {
  previewModal.hidden = true;
  previewModal.setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
  previewModalImage.onload = null;
  previewModalImage.removeAttribute("src");
  previewModalBody.classList.remove("is-loading");
}

previewModal.addEventListener("click", (e) => {
  if (e.target.closest("[data-close-preview]")) closePreviewModal();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !previewModal.hidden) closePreviewModal();
});

function showToast(message) {
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => {
    toast.hidden = true;
  }, 2600);
}

function showError(message) {
  formError.textContent = message;
  formError.hidden = !message;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let detail = "요청 처리 중 오류가 발생했습니다.";
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch (_) {}
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return null;
  const type = res.headers.get("content-type") || "";
  if (type.includes("application/json")) return res.json();
  return res.blob();
}

async function loadBrands() {
  brands = await api("/api/brands");
  if (!brands.some((b) => b.id === activeBrand)) {
    activeBrand = brands[0]?.id || DEFAULT_BRAND;
  }
  renderBrandTabs();
}

async function loadLabels() {
  const data = await api(`/api/labels${brandQuery()}`);
  labels.clear();
  data.forEach((item) => labels.set(item.id, item));
  renderSheet();
}

async function loadCatalog() {
  try {
    catalog = await api(`/api/catalog${brandQuery()}`);
    renderCatalog();
  } catch (err) {
    catalog = [];
    catalogMeta.textContent = `카탈로그를 불러오지 못했습니다. (${err.message})`;
    catalogList.innerHTML = "";
  }
}

function renderCatalog() {
  const validCount = catalog.filter((item) => item.valid).length;
  catalogMeta.textContent = `[${currentBrandInfo().label}] 총 ${catalog.length}개 · 사용 가능 ${validCount}개 · 선택 ${catalogSelected.size}개`;

  const selectedValid = [...catalogSelected].filter((code) => {
    const item = catalog.find((c) => c.code === code);
    return item?.valid;
  });
  importCatalogBtn.disabled = selectedValid.length === 0;

  catalogList.innerHTML = "";
  catalog.forEach((item) => {
    const el = document.createElement("div");
    el.className = "catalog-item";
    if (!item.valid) el.classList.add("invalid");
    if (catalogSelected.has(item.code)) el.classList.add("selected");

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = catalogSelected.has(item.code);
    checkbox.disabled = !item.valid;
    checkbox.addEventListener("change", (e) => {
      e.stopPropagation();
      if (checkbox.checked) catalogSelected.add(item.code);
      else catalogSelected.delete(item.code);
      renderCatalog();
    });

    const body = document.createElement("div");
    body.className = "catalog-item-body";
    body.innerHTML = `
      <strong>${item.code} ${item.name || "(상품명 미인식)"}</strong>
      <span>${item.barcode || "바코드 없음"}</span>
      <span class="catalog-badge ${item.valid ? "ok" : "warn"}">${item.valid ? "사용 가능" : "확인 필요"}</span>
    `;

    el.appendChild(checkbox);
    el.appendChild(body);
    el.addEventListener("click", (e) => {
      if (!item.valid || e.target instanceof HTMLInputElement) return;
      if (catalogSelected.has(item.code)) catalogSelected.delete(item.code);
      else catalogSelected.add(item.code);
      renderCatalog();
    });

    catalogList.appendChild(el);
  });
}

function coloridiumRowLabel(row, slotMap) {
  let firstCode = null;
  for (let col = 0; col < SHEET_COLS; col += 1) {
    const item = slotMap.get(`${row}-${col}`);
    if (item?.code && /^\d+$/.test(item.code)) {
      firstCode = parseInt(item.code, 10);
      break;
    }
  }
  if (firstCode != null) {
    return String(Math.floor(firstCode / 10) * 10).padStart(3, "0");
  }
  return String(row * 100).padStart(3, "0");
}

function nailflowerRowLabel(row, slotMap) {
  let firstCode = null;
  for (let col = 0; col < SHEET_COLS; col += 1) {
    const item = slotMap.get(`${row}-${col}`);
    if (item?.code && /^\d+$/.test(item.code)) {
      firstCode = parseInt(item.code, 10);
      break;
    }
  }
  if (firstCode != null) {
    const tag = firstCode < 10 ? 0 : Math.floor(firstCode / 10) * 10;
    return tag === 0 ? "00" : String(tag);
  }
  return row === 0 ? "00" : String(row * 100);
}

function rowLabel(row, slotMap) {
  if (activeBrand === "coloridium") return coloridiumRowLabel(row, slotMap);
  if (activeBrand === "nailflower") return nailflowerRowLabel(row, slotMap);
  return `S${row * 10}`;
}

function renderSheet() {
  sheetMeta.textContent = `${labels.size} / ${SHEET_CAPACITY}`;
  selectedCount.textContent = String(selected.size);
  exportBtn.disabled = selected.size === 0;

  const slotMap = new Map();
  labels.forEach((item) => slotMap.set(`${item.row}-${item.col}`, item));

  sheetGrid.innerHTML = "";
  for (let row = 0; row < SHEET_ROWS; row += 1) {
    const rowEl = document.createElement("div");
    rowEl.className = "sheet-row";

    const labelEl = document.createElement("div");
    labelEl.className = "row-label";
    labelEl.textContent = rowLabel(row, slotMap);
    labelEl.title = `${row + 1}행`;
    rowEl.appendChild(labelEl);

    const cellsEl = document.createElement("div");
    cellsEl.className = "sheet-row-cells";

    for (let col = 0; col < SHEET_COLS; col += 1) {
      const key = `${row}-${col}`;
      const item = slotMap.get(key);
      const cell = document.createElement("div");
      cell.className = "cell";

      if (!item) {
        cell.classList.add("empty");
        cell.textContent = `${row + 1}-${col + 1}`;
        cellsEl.appendChild(cell);
        continue;
      }

      cell.classList.add("filled");
      if (selected.has(item.id)) cell.classList.add("selected");

      const img = document.createElement("img");
      img.alt = `${item.code} ${item.name}`;
      img.src = `/api/labels/${item.id}/preview?brand=${activeBrand}&t=${Date.now()}`;
      cell.appendChild(img);

      const overlay = document.createElement("div");
      overlay.className = "cell-overlay";

      const top = document.createElement("div");
      top.className = "cell-top";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "cell-check";
      checkbox.checked = selected.has(item.id);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selected.add(item.id);
        else selected.delete(item.id);
        renderSheet();
      });

      const del = document.createElement("button");
      del.type = "button";
      del.className = "cell-delete";
      del.title = "삭제";
      del.textContent = "×";
      del.addEventListener("click", async (e) => {
        e.stopPropagation();
        await api(`/api/labels/${item.id}${brandQuery()}`, { method: "DELETE" });
        selected.delete(item.id);
        labels.delete(item.id);
        showToast("라벨을 삭제했습니다.");
        renderSheet();
      });

      top.appendChild(checkbox);
      top.appendChild(del);

      const caption = document.createElement("div");
      caption.className = "cell-caption";
      caption.textContent = `${item.code} ${item.name}`;

      overlay.appendChild(top);
      overlay.appendChild(caption);
      cell.appendChild(overlay);

      cell.addEventListener("click", (e) => {
        if (e.target instanceof HTMLInputElement || e.target instanceof HTMLButtonElement) return;
        openPreviewModal(item);
      });

      cellsEl.appendChild(cell);
    }

    rowEl.appendChild(cellsEl);
    sheetGrid.appendChild(rowEl);
  }
}

document.getElementById("addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("");

  const code = document.getElementById("code").value.trim();
  const name = document.getElementById("name").value.trim();
  const barcode = document.getElementById("barcode").value.trim();

  try {
    const item = await api(`/api/labels${brandQuery()}`, {
      method: "POST",
      body: JSON.stringify({ code, name, barcode }),
    });
    labels.set(item.id, item);
    selected.add(item.id);
    e.target.reset();
    showToast(`${item.code} 라벨을 추가했습니다.`);
    renderSheet();
  } catch (err) {
    showError(err.message);
  }
});

document.getElementById("refreshCatalogBtn").addEventListener("click", async () => {
  const btn = document.getElementById("refreshCatalogBtn");
  btn.disabled = true;
  btn.textContent = "추출 중...";
  try {
    catalog = await api(`/api/catalog/refresh${brandQuery()}`, { method: "POST" });
    catalogSelected.clear();
    renderCatalog();
    await loadLabels();
    showToast(`[${currentBrandInfo().label}] AI 추출 ${catalog.length}개 · 시트 ${labels.size}개 반영`);
  } catch (err) {
    showToast(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "AI 재추출";
  }
});

document.getElementById("selectAllCatalogBtn").addEventListener("click", () => {
  catalog.filter((item) => item.valid).forEach((item) => catalogSelected.add(item.code));
  renderCatalog();
});

importCatalogBtn.addEventListener("click", async () => {
  const codes = [...catalogSelected].filter((code) => catalog.find((c) => c.code === code)?.valid);
  if (!codes.length) return;

  importCatalogBtn.disabled = true;
  try {
    const result = await api("/api/catalog/import", {
      method: "POST",
      body: JSON.stringify({ codes, brand: activeBrand }),
    });
    result.imported.forEach((item) => {
      labels.set(item.id, item);
      selected.add(item.id);
    });
    renderSheet();
    if (result.errors?.length) {
      showToast(`${result.imported.length}개 추가 · ${result.errors.length}개 실패`);
    } else {
      showToast(`${result.imported.length}개 항목을 시트에 추가했습니다.`);
    }
  } catch (err) {
    showToast(err.message);
  } finally {
    renderCatalog();
  }
});

document.getElementById("selectAllBtn").addEventListener("click", () => {
  labels.forEach((item) => selected.add(item.id));
  renderSheet();
});

document.getElementById("clearSelectBtn").addEventListener("click", () => {
  selected.clear();
  renderSheet();
});

document.getElementById("clearSheetBtn").addEventListener("click", async () => {
  if (!labels.size) return;
  if (!confirm(`[${currentBrandInfo().label}] 시트의 모든 라벨을 삭제할까요?`)) return;
  await api(`/api/labels${brandQuery()}`, { method: "DELETE" });
  labels.clear();
  selected.clear();
  showToast("시트를 비웠습니다.");
  renderSheet();
});

document.getElementById("exportBtn").addEventListener("click", async () => {
  if (!selected.size) return;

  const mode = document.querySelector('input[name="exportMode"]:checked')?.value || "individual";
  exportBtn.disabled = true;
  exportBtn.textContent = "생성 중...";

  try {
    const blob = await api("/api/export", {
      method: "POST",
      body: JSON.stringify({ ids: [...selected], mode, brand: activeBrand }),
    });

    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = mode === "sheet" ? `selected_labels_${activeBrand}_sheet.zip` : `selected_labels_${activeBrand}.zip`;
    a.click();
    URL.revokeObjectURL(url);
    showToast("내보내기가 완료되었습니다.");
  } catch (err) {
    showToast(err.message);
  } finally {
    exportBtn.disabled = selected.size === 0;
    exportBtn.textContent = "선택 항목 내보내기";
  }
});

Promise.all([loadBrands(), loadLabels(), loadCatalog(), checkServerVersion()]).catch((err) => showToast(err.message));

async function checkServerVersion() {
  try {
    const health = await api("/api/health");
    if (!health.catalog || (health.brands || 0) < 3) {
      showToast("서버를 재시작해 최신 버전을 적용해 주세요.");
    }
  } catch (_) {
    /* health 실패는 loadCatalog에서 처리 */
  }
}
