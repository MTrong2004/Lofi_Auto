(function () {
  const payloadNode = document.getElementById("live-preview-payload");
  if (!payloadNode) return;
  const payload = JSON.parse(payloadNode.textContent || "{}");
  const background = document.getElementById("live-preview-background");
  const effect = document.getElementById("live-preview-effect");
  const canvas = document.getElementById("live-preview-canvas");
  const status = document.getElementById("live-preview-status");
  const note = document.getElementById("live-preview-note");

  background.src = payload.imageSrc || "";
  background.classList.add("motion-" + (payload.motionMode || "smooth_zoom"));
  effect.src = payload.effectSrc || "";
  effect.playbackRate = Number(payload.speed || 1.0);

  const useChroma = payload.effectType === "chroma_key";

  if (!useChroma) {
    // CSS blending đủ chính xác cho overlay nền đen / alpha / video thường.
    effect.style.opacity = String(payload.opacity ?? 0.55);
    if (payload.effectType === "alpha" || payload.effectType === "normal") {
      effect.style.mixBlendMode = "normal";
    } else {
      effect.style.mixBlendMode = payload.blendMode || "screen";
    }
  } else {
    // Chroma key thật bằng WebGL: CSS blend mode không tách được phông xanh.
    effect.style.display = "none";
    canvas.style.display = "block";
    canvas.width = payload.canvasWidth || 640;
    canvas.height = payload.canvasHeight || 360;

    const gl = canvas.getContext("webgl", { premultipliedAlpha: false, alpha: true });
    if (!gl) {
      // Máy không có WebGL: quay về CSS screen để ít nhất vẫn thấy hiệu ứng.
      canvas.style.display = "none";
      effect.style.display = "";
      effect.style.opacity = String(payload.opacity ?? 0.55);
      effect.style.mixBlendMode = "screen";
      if (note) note.textContent = "Trình duyệt không hỗ trợ WebGL, preview tạm dùng blend screen.";
    } else {
      const vertexSrc = [
        "attribute vec2 aPos;",
        "varying vec2 vUV;",
        "void main() {",
        "  vUV = vec2((aPos.x + 1.0) * 0.5, 1.0 - (aPos.y + 1.0) * 0.5);",
        "  gl_Position = vec4(aPos, 0.0, 1.0);",
        "}",
      ].join("\n");
      // Key theo khoảng cách màu trong mặt phẳng CbCr (giống filter chromakey của FFmpeg).
      const fragmentSrc = [
        "precision mediump float;",
        "varying vec2 vUV;",
        "uniform sampler2D uTex;",
        "uniform vec3 uKey;",
        "uniform float uSimilarity;",
        "uniform float uSoftness;",
        "uniform float uDespill;",
        "uniform float uOpacity;",
        "uniform float uMatte;",
        "vec2 toCC(vec3 c) {",
        "  float cb = 0.5 - 0.168736 * c.r - 0.331264 * c.g + 0.5 * c.b;",
        "  float cr = 0.5 + 0.5 * c.r - 0.418688 * c.g - 0.081312 * c.b;",
        "  return vec2(cb, cr);",
        "}",
        "void main() {",
        "  vec4 tex = texture2D(uTex, vUV);",
        "  float dist = distance(toCC(tex.rgb), toCC(uKey));",
        "  float alpha = smoothstep(uSimilarity, uSimilarity + max(uSoftness, 0.0001), dist);",
        "  vec3 rgb = tex.rgb;",
        "  if (uDespill > 0.0) {",
        "    if (uKey.g >= uKey.r && uKey.g >= uKey.b) {",
        "      float spill = max(rgb.g - max(rgb.r, rgb.b), 0.0);",
        "      rgb.g -= spill * uDespill;",
        "    } else if (uKey.b > uKey.g) {",
        "      float spill = max(rgb.b - max(rgb.r, rgb.g), 0.0);",
        "      rgb.b -= spill * uDespill;",
        "    }",
        "  }",
        "  vec4 keyed = vec4(rgb, alpha * uOpacity);",
        "  vec4 matte = vec4(vec3(alpha), 1.0);",
        "  gl_FragColor = mix(keyed, matte, uMatte);",
        "}",
      ].join("\n");

      function compile(type, source) {
        const shader = gl.createShader(type);
        gl.shaderSource(shader, source);
        gl.compileShader(shader);
        return shader;
      }
      const program = gl.createProgram();
      gl.attachShader(program, compile(gl.VERTEX_SHADER, vertexSrc));
      gl.attachShader(program, compile(gl.FRAGMENT_SHADER, fragmentSrc));
      gl.linkProgram(program);
      gl.useProgram(program);

      const buffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
      const aPos = gl.getAttribLocation(program, "aPos");
      gl.enableVertexAttribArray(aPos);
      gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

      const texture = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);

      const key = payload.keyColor || [0.0, 1.0, 0.0];
      gl.uniform3f(gl.getUniformLocation(program, "uKey"), key[0], key[1], key[2]);
      gl.uniform1f(gl.getUniformLocation(program, "uSimilarity"), Number(payload.similarity || 0.18));
      gl.uniform1f(gl.getUniformLocation(program, "uSoftness"), Number(payload.softness || 0.08));
      gl.uniform1f(gl.getUniformLocation(program, "uDespill"), Number(payload.despill || 0.35));
      gl.uniform1f(gl.getUniformLocation(program, "uOpacity"), Number(payload.opacity ?? 0.85));
      gl.uniform1f(gl.getUniformLocation(program, "uMatte"), payload.showMatte ? 1.0 : 0.0);
      gl.uniform1i(gl.getUniformLocation(program, "uTex"), 0);

      gl.clearColor(0, 0, 0, 0);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

      let rafId = null;
      function renderFrame() {
        if (effect.readyState >= 2) {
          gl.bindTexture(gl.TEXTURE_2D, texture);
          try {
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, effect);
            gl.clear(gl.COLOR_BUFFER_BIT);
            gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
          } catch (err) {
            /* video frame chưa sẵn sàng: bỏ qua frame này */
          }
        }
        rafId = window.requestAnimationFrame(renderFrame);
      }
      renderFrame();
      window.addEventListener("beforeunload", function () {
        if (rafId) window.cancelAnimationFrame(rafId);
      });
      if (note) {
        note.textContent = payload.showMatte
          ? "Đang xem matte: vùng trắng được giữ, vùng đen bị xóa."
          : "Chroma key WebGL. Feather viền chỉ áp trong FFmpeg preview/render.";
      }
    }
  }

  effect.addEventListener("loadedmetadata", function () {
    effect.playbackRate = Number(payload.speed || 1.0);
    effect.play().catch(function () {
      status.textContent = "BẤM ĐỂ PHÁT";
      status.style.cursor = "pointer";
      status.addEventListener("click", function () { effect.play(); }, { once: true });
    });
  });
  effect.addEventListener("error", function () {
    status.textContent = "KHÔNG PHÁT ĐƯỢC";
  });

  if (payload.text) {
    const stage = document.getElementById("live-preview-shell").querySelector(".live-preview-stage");
    const textContainer = document.createElement("div");
    textContainer.className = "live-preview-text-container";

    const pos = payload.text.position || "bottom_center";
    if (pos.startsWith("bottom_")) {
      textContainer.style.alignItems = "flex-end";
    } else if (pos.startsWith("center")) {
      textContainer.style.alignItems = "center";
    } else {
      textContainer.style.alignItems = "flex-start";
    }

    if (pos.endsWith("_left")) {
      textContainer.style.justifyContent = "flex-start";
    } else if (pos.endsWith("_right")) {
      textContainer.style.justifyContent = "flex-end";
    } else {
      textContainer.style.justifyContent = "center";
    }

    const textEl = document.createElement("div");
    textEl.className = "live-preview-text-content";
    textEl.innerText = payload.text.content;

    const fontStacks = {
      "sans": "'Segoe UI','Microsoft YaHei',Arial,sans-serif",
      "serif": "'Times New Roman','SimSun',Georgia,serif",
      "display": "'Arial Black','Microsoft YaHei',Impact,sans-serif",
    };

    textEl.style.fontFamily = fontStacks[payload.text.fontStyle] || fontStacks["sans"];
    textEl.style.color = payload.text.textColor;
    textEl.style.fontWeight = payload.text.bold ? "bold" : "normal";
    textEl.style.opacity = "0";
    textEl.style.transform = "translate3d(0, 0, 0) scale(0.96)";
    textEl.style.filter = "blur(0px)";

    if (pos.endsWith("_left")) {
      textEl.style.textAlign = "left";
    } else if (pos.endsWith("_right")) {
      textEl.style.textAlign = "right";
    } else {
      textEl.style.textAlign = "center";
    }

    const updateSize = () => {
      const stageWidth = stage.clientWidth || 640;
      const scale = stageWidth / 1920;

      const fontSize = payload.text.fontSize || 72;
      textEl.style.fontSize = (fontSize * scale) + "px";

      const outlineWidth = payload.text.outlineWidth || 2.0;
      const scaledOutline = (outlineWidth * scale);
      textEl.style.webkitTextStroke = `${scaledOutline}px ${payload.text.outlineColor}`;
      textEl.style.paintOrder = "stroke fill";
    };

    const introEffect = payload.text.introEffect || "fade";
    const introDuration = Math.max(220, Math.round(Number(payload.text.introDuration || 0.8) * 1000));
    const outroDuration = Math.max(220, Math.round(Number(payload.text.outroDuration || 1.0) * 1000));

    const introKeyframes = (() => {
      switch (introEffect) {
        case "blur_in":
          return [{ opacity: 0, filter: "blur(10px)" }, { opacity: 1, filter: "blur(0px)" }];
        case "scale_slow":
          return [{ opacity: 0, transform: "translate3d(0, 0, 0) scale(0.92)" }, { opacity: 1, transform: "translate3d(0, 0, 0) scale(1)" }];
        case "slide_up":
          return [{ opacity: 0, transform: "translate3d(0, 16px, 0) scale(1)" }, { opacity: 1, transform: "translate3d(0, 0, 0) scale(1)" }];
        case "slide_left":
          return [{ opacity: 0, transform: "translate3d(16px, 0, 0) scale(1)" }, { opacity: 1, transform: "translate3d(0, 0, 0) scale(1)" }];
        default:
          return [{ opacity: 0 }, { opacity: 1 }];
      }
    })();

    textEl.animate(introKeyframes, {
      duration: introDuration,
      easing: "cubic-bezier(0.2, 0.8, 0.2, 1)",
      fill: "forwards",
    });

    if ((payload.text.holdEffect || "none") === "soft_glow") {
      textEl.animate([
        { textShadow: "0 0 0 rgba(255,255,255,0)" },
        { textShadow: "0 0 14px rgba(255,255,255,0.45)" },
        { textShadow: "0 0 0 rgba(255,255,255,0)" },
      ], {
        duration: 1600,
        iterations: Infinity,
        direction: "alternate",
        easing: "ease-in-out",
      });
    }

    if ((payload.text.outroEffect || "fade") === "dissolve") {
      window.setTimeout(() => {
        textEl.animate([
          { opacity: 1, filter: "blur(0px)" },
          { opacity: 0, filter: "blur(4px)" },
        ], {
          duration: outroDuration,
          easing: "ease-in",
          fill: "forwards",
        });
      }, Math.max(1200, introDuration + 800));
    } else if ((payload.text.outroEffect || "fade") === "fade") {
      window.setTimeout(() => {
        textEl.animate([{ opacity: 1 }, { opacity: 0 }], {
          duration: outroDuration,
          easing: "ease-in",
          fill: "forwards",
        });
      }, Math.max(1200, introDuration + 800));
    }

    updateSize();
    window.addEventListener("resize", updateSize);

    textContainer.appendChild(textEl);
    stage.appendChild(textContainer);
  }
})();

