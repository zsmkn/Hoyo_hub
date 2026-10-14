/* 2.5D 視差檢視器：原圖 + 深度圖 → WebGL shader 依深度位移像素 */
class ParallaxViewer {
  constructor(canvas) {
    this.canvas = canvas;
    const gl = canvas.getContext("webgl", { preserveDrawingBuffer: true, premultipliedAlpha: false, alpha: true });
    if (!gl) throw new Error("你的瀏覽器不支援 WebGL");
    this.gl = gl;
    this.strength = 0.04;
    this.focus = 0.5;
    this.mode = "mouse";
    this.showDepth = false;
    this.pointer = [0, 0];   // 目標位置
    this.offset = [0, 0];    // 平滑後的位置
    this.loaded = false;
    this._initGL();
    this._bindInput();
    this._loop = this._loop.bind(this);
    requestAnimationFrame(this._loop);
  }

  _initGL() {
    const gl = this.gl;
    const vs = `
      attribute vec2 aPos;
      varying vec2 vUv;
      void main() { vUv = (aPos + 1.0) * 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }`;
    // 以固定點迭代近似「反向視差映射」：找出哪個來源像素會被位移到這裡
    const fs = `
      precision highp float;
      varying vec2 vUv;
      uniform sampler2D uImg, uDepth;
      uniform vec2 uOffset;
      uniform float uStrength, uFocus, uShowDepth, uZoom;
      void main() {
        vec2 uv = (vUv - 0.5) / uZoom + 0.5;
        vec2 shift = uOffset * uStrength;
        vec2 p = uv;
        for (int i = 0; i < 8; i++) {
          float d = texture2D(uDepth, p).r;
          p = uv - shift * (d - uFocus);
        }
        p = clamp(p, 0.001, 0.999);
        gl_FragColor = uShowDepth > 0.5 ? vec4(vec3(texture2D(uDepth, p).r), 1.0) : texture2D(uImg, p);
      }`;
    const prog = gl.createProgram();
    for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      gl.attachShader(prog, s);
    }
    gl.linkProgram(prog);
    gl.useProgram(prog);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, "aPos");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    this.u = {};
    for (const n of ["uImg", "uDepth", "uOffset", "uStrength", "uFocus", "uShowDepth", "uZoom"]) this.u[n] = gl.getUniformLocation(prog, n);
    gl.uniform1i(this.u.uImg, 0);
    gl.uniform1i(this.u.uDepth, 1);
    this.tex = [gl.createTexture(), gl.createTexture()];
  }

  _bindInput() {
    const setFromEvent = (e) => {
      if (this.mode !== "mouse") return;
      const r = this.canvas.getBoundingClientRect();
      this.pointer = [((e.clientX - r.left) / r.width) * 2 - 1, -(((e.clientY - r.top) / r.height) * 2 - 1)];
    };
    this.canvas.addEventListener("pointermove", setFromEvent);
    this.canvas.addEventListener("pointerdown", setFromEvent);
    this.canvas.addEventListener("pointerleave", () => { if (this.mode === "mouse") this.pointer = [0, 0]; });
    window.addEventListener("deviceorientation", (e) => {
      if (this.mode !== "gyro" || e.gamma == null) return;
      const clamp = (v) => Math.max(-1, Math.min(1, v));
      this.pointer = [clamp(e.gamma / 25), clamp((e.beta - 45) / 25)];
    });
  }

  async enableGyro() {
    // iOS 需要使用者授權
    if (typeof DeviceOrientationEvent !== "undefined" && DeviceOrientationEvent.requestPermission) {
      const res = await DeviceOrientationEvent.requestPermission();
      if (res !== "granted") throw new Error("未取得陀螺儀權限");
    }
  }

  _loadImage(url) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("圖片載入失敗"));
      img.src = url;
    });
  }

  _upload(unit, img) {
    const gl = this.gl;
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, this.tex[unit]);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  }

  async load(imageUrl, depthUrl) {
    const [img, depth] = await Promise.all([this._loadImage(imageUrl), this._loadImage(depthUrl)]);
    this.canvas.width = img.naturalWidth;
    this.canvas.height = img.naturalHeight;
    this.gl.viewport(0, 0, img.naturalWidth, img.naturalHeight);
    this._upload(0, img);
    this._upload(1, depth);
    this.loaded = true;
  }

  _autoPointer(t) {
    // 週期 4 秒、首尾相接的 8 字形擺動，錄影時可無縫循環
    const a = (t / 4000) * Math.PI * 2;
    return [Math.sin(a) * 0.9, Math.sin(a * 2) * 0.35];
  }

  _loop(t) {
    requestAnimationFrame(this._loop);
    if (!this.loaded) return;
    if (this.mode === "auto" || this.recording) {
      this.offset = this._autoPointer(this.recording ? t - this.recordStart : t);
    } else {
      this.offset[0] += (this.pointer[0] - this.offset[0]) * 0.12;
      this.offset[1] += (this.pointer[1] - this.offset[1]) * 0.12;
    }
    const gl = this.gl;
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform2f(this.u.uOffset, this.offset[0], this.offset[1]);
    gl.uniform1f(this.u.uStrength, this.strength);
    gl.uniform1f(this.u.uFocus, this.focus);
    gl.uniform1f(this.u.uShowDepth, this.showDepth ? 1 : 0);
    gl.uniform1f(this.u.uZoom, 1 + this.strength * 1.1);  // 稍微放大，避免邊緣露出
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  /* 錄一段 4 秒無縫循環影片（WebM） */
  record(seconds = 4) {
    if (!window.MediaRecorder || !this.canvas.captureStream) return Promise.reject(new Error("瀏覽器不支援錄影"));
    const stream = this.canvas.captureStream(30);
    const mime = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"].find((m) => MediaRecorder.isTypeSupported(m));
    const rec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 8_000_000 });
    const chunks = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    return new Promise((resolve) => {
      rec.onstop = () => { this.recording = false; resolve(new Blob(chunks, { type: "video/webm" })); };
      this.recordStart = performance.now();
      this.recording = true;
      rec.start();
      setTimeout(() => rec.stop(), seconds * 1000);
    });
  }
}
window.ParallaxViewer = ParallaxViewer;
