(function () {
  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function parseConfig(value, fallback) {
    if (!value || !value.trim()) {
      return JSON.parse(fallback);
    }

    try {
      return JSON.parse(value);
    } catch {
      return JSON.parse(fallback);
    }
  }

  function ensurePlacement(config) {
    if (!config.placement || typeof config.placement !== "object") {
      config.placement = {};
    }

    config.placement.x = Number(config.placement.x ?? 100) || 100;
    config.placement.y = Number(config.placement.y ?? 100) || 100;
    config.placement.width = Number(config.placement.width ?? 300) || 300;
    config.placement.height = Number(config.placement.height ?? 300) || 300;
    config.placement.fit = config.placement.fit || "contain";
    config.placement.rotation = Number(config.placement.rotation ?? 0) || 0;
    config.placement.opacity = Number(config.placement.opacity ?? 1) || 1;
    config.placement.corner_radius = Number(config.placement.corner_radius ?? 0) || 0;
  }

  function ensureSamples(config) {
    if (!Array.isArray(config.sample_placements)) {
      config.sample_placements = [];
    }
  }

  function updateTextarea(textarea, config) {
    textarea.value = JSON.stringify(config, null, 2);
  }

  function renderSamples(root, config, applySample) {
    const list = root.querySelector('[data-role="sample-list"]');
    if (!list) return;

    list.innerHTML = "";
    if (!config.sample_placements.length) {
      const empty = document.createElement("div");
      empty.className = "mockup-config-editor__hint";
      empty.textContent = "No sample placements added yet.";
      list.appendChild(empty);
      return;
    }

    config.sample_placements.forEach((sample, index) => {
      const item = document.createElement("div");
      item.className = "mockup-config-editor__sample-item";

      const meta = document.createElement("div");
      meta.className = "mockup-config-editor__sample-meta";
      const title = document.createElement("strong");
      title.textContent = sample.label || `Sample ${index + 1}`;
      const details = document.createElement("span");
      details.textContent = `${sample.placement.width}w × ${sample.placement.height}h at ${sample.placement.x}, ${sample.placement.y}`;
      meta.appendChild(title);
      meta.appendChild(details);

      const buttons = document.createElement("div");
      buttons.className = "mockup-config-editor__sample-buttons";

      const useButton = document.createElement("button");
      useButton.type = "button";
      useButton.className = "button";
      useButton.textContent = "Use";
      useButton.addEventListener("click", function () {
        applySample(sample);
      });

      const removeButton = document.createElement("button");
      removeButton.type = "button";
      removeButton.className = "button";
      removeButton.textContent = "Remove";
      removeButton.addEventListener("click", function () {
        config.sample_placements.splice(index, 1);
        root.dispatchEvent(new CustomEvent("mockup-config-updated"));
      });

      buttons.appendChild(useButton);
      buttons.appendChild(removeButton);
      item.appendChild(meta);
      item.appendChild(buttons);
      list.appendChild(item);
    });
  }

  function initEditor(root) {
    const textarea = root.querySelector("textarea");
    const image = root.querySelector('[data-role="preview-image"]');
    const canvas = root.querySelector('[data-role="canvas"]');
    const overlay = root.querySelector('[data-role="overlay"]');
    const selection = root.querySelector('[data-role="selection"]');
    const resizeHandle = root.querySelector('[data-role="resize-handle"]');
    const fieldX = root.querySelector('[data-role="field-x"]');
    const fieldY = root.querySelector('[data-role="field-y"]');
    const fieldWidth = root.querySelector('[data-role="field-width"]');
    const fieldHeight = root.querySelector('[data-role="field-height"]');
    const fieldCornerRadius = root.querySelector('[data-role="field-corner-radius"]');
    const sampleLabelInput = root.querySelector('[data-role="sample-label"]');
    const addSampleButton = root.querySelector('[data-role="add-sample"]');
    const baseImageInput = document.getElementById("id_base_image");

    if (!textarea || !image || !canvas || !overlay || !selection || !resizeHandle) {
      return;
    }

    const fallback = root.dataset.emptyConfig || "{}";
    const config = parseConfig(textarea.value, fallback);
    ensurePlacement(config);
    ensureSamples(config);

    let naturalWidth = 0;
    let naturalHeight = 0;
    let interaction = null;

    function syncFields() {
      fieldX.value = Math.round(config.placement.x);
      fieldY.value = Math.round(config.placement.y);
      fieldWidth.value = Math.round(config.placement.width);
      fieldHeight.value = Math.round(config.placement.height);
      fieldCornerRadius.value = Math.round(config.placement.corner_radius || 0);
    }

    function syncSelection() {
      if (!naturalWidth || !naturalHeight) {
        return;
      }

      const scaleX = canvas.clientWidth / naturalWidth;
      const scaleY = canvas.clientHeight / naturalHeight;
      selection.style.left = `${config.placement.x * scaleX}px`;
      selection.style.top = `${config.placement.y * scaleY}px`;
      selection.style.width = `${config.placement.width * scaleX}px`;
      selection.style.height = `${config.placement.height * scaleY}px`;
      selection.style.borderRadius = `${(config.placement.corner_radius || 0) * ((scaleX + scaleY) / 2)}px`;
    }

    function syncAll() {
      ensurePlacement(config);
      ensureSamples(config);
      updateTextarea(textarea, config);
      syncFields();
      syncSelection();
      renderSamples(root, config, function (sample) {
        config.placement = { ...sample.placement };
        root.dispatchEvent(new CustomEvent("mockup-config-updated"));
      });
    }

    function setEmptyState(isEmpty) {
      root.classList.toggle("mockup-config-editor--empty", isEmpty);
    }

    function handleImageReady() {
      naturalWidth = image.naturalWidth;
      naturalHeight = image.naturalHeight;
      setEmptyState(!naturalWidth || !naturalHeight);
      syncSelection();
    }

    function loadPreview(src) {
      if (!src) {
        image.removeAttribute("src");
        naturalWidth = 0;
        naturalHeight = 0;
        setEmptyState(true);
        return;
      }

      image.src = src;
      image.onload = handleImageReady;
      image.onerror = function () {
        naturalWidth = 0;
        naturalHeight = 0;
        setEmptyState(true);
      };
    }

    function updatePlacementFromFields() {
      config.placement.x = Math.max(0, Number(fieldX.value) || 0);
      config.placement.y = Math.max(0, Number(fieldY.value) || 0);
      config.placement.width = Math.max(24, Number(fieldWidth.value) || 24);
      config.placement.height = Math.max(24, Number(fieldHeight.value) || 24);
      config.placement.corner_radius = Math.max(0, Number(fieldCornerRadius.value) || 0);
      root.dispatchEvent(new CustomEvent("mockup-config-updated"));
    }

    [fieldX, fieldY, fieldWidth, fieldHeight, fieldCornerRadius].forEach(function (field) {
      if (!field) return;
      field.addEventListener("input", updatePlacementFromFields);
    });

    function pointerMove(event) {
      if (!interaction || !naturalWidth || !naturalHeight) {
        return;
      }

      const rect = canvas.getBoundingClientRect();
      const scaleX = naturalWidth / rect.width;
      const scaleY = naturalHeight / rect.height;
      const deltaX = (event.clientX - interaction.startX) * scaleX;
      const deltaY = (event.clientY - interaction.startY) * scaleY;

      if (interaction.mode === "move") {
        config.placement.x = clamp(interaction.startPlacement.x + deltaX, 0, naturalWidth - config.placement.width);
        config.placement.y = clamp(interaction.startPlacement.y + deltaY, 0, naturalHeight - config.placement.height);
      } else if (interaction.mode === "resize") {
        config.placement.width = clamp(interaction.startPlacement.width + deltaX, 24, naturalWidth - config.placement.x);
        config.placement.height = clamp(interaction.startPlacement.height + deltaY, 24, naturalHeight - config.placement.y);
      }

      syncAll();
    }

    function pointerUp() {
      interaction = null;
      document.removeEventListener("pointermove", pointerMove);
      document.removeEventListener("pointerup", pointerUp);
    }

    selection.addEventListener("pointerdown", function (event) {
      if (event.target === resizeHandle) {
        return;
      }

      event.preventDefault();
      interaction = {
        mode: "move",
        startX: event.clientX,
        startY: event.clientY,
        startPlacement: { ...config.placement },
      };
      document.addEventListener("pointermove", pointerMove);
      document.addEventListener("pointerup", pointerUp);
    });

    resizeHandle.addEventListener("pointerdown", function (event) {
      event.preventDefault();
      event.stopPropagation();
      interaction = {
        mode: "resize",
        startX: event.clientX,
        startY: event.clientY,
        startPlacement: { ...config.placement },
      };
      document.addEventListener("pointermove", pointerMove);
      document.addEventListener("pointerup", pointerUp);
    });

    addSampleButton.addEventListener("click", function () {
      const label = (sampleLabelInput.value || "").trim() || `Sample ${config.sample_placements.length + 1}`;
      config.sample_placements.push({
        label: label,
        placement: {
          x: Math.round(config.placement.x),
          y: Math.round(config.placement.y),
          width: Math.round(config.placement.width),
          height: Math.round(config.placement.height),
          corner_radius: Math.round(config.placement.corner_radius || 0),
        },
      });
      sampleLabelInput.value = "";
      root.dispatchEvent(new CustomEvent("mockup-config-updated"));
    });

    root.addEventListener("mockup-config-updated", syncAll);
    window.addEventListener("resize", syncSelection);

    if (baseImageInput) {
      baseImageInput.addEventListener("change", function (event) {
        const file = event.target.files && event.target.files[0];
        if (!file) {
          return;
        }
        loadPreview(URL.createObjectURL(file));
      });
    }

    textarea.addEventListener("change", function () {
      const nextConfig = parseConfig(textarea.value, fallback);
      Object.keys(config).forEach(function (key) {
        delete config[key];
      });
      Object.assign(config, nextConfig);
      root.dispatchEvent(new CustomEvent("mockup-config-updated"));
    });

    loadPreview(root.dataset.baseImage || "");
    syncAll();
  }

  document.addEventListener("DOMContentLoaded", function () {
    document
      .querySelectorAll(".mockup-config-editor")
      .forEach(function (element) {
        initEditor(element);
      });
  });
})();
