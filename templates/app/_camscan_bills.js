// ---- Scan with the phone camera (only shown where the browser can read barcodes; a USB scanner keeps working as before) ----
(function (input) {
  if (!input || !('BarcodeDetector' in window) || !window.isSecureContext || !(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) return;
  const btn = document.createElement('button');
  btn.type = 'button'; btn.className = 'btn ghost sm'; btn.id = 'cam-open'; btn.textContent = '📷 Scan with camera';
  btn.style.marginTop = '6px';
  input.insertAdjacentElement('afterend', btn);
  const dlg = document.createElement('dialog');
  dlg.id = 'cam-dialog';
  dlg.style.cssText = 'border:0;border-radius:14px;padding:14px;width:min(420px,94vw);background:var(--card);color:var(--ink)';
  dlg.innerHTML = '<h3 style="margin:0 0 8px">Scan a barcode</h3>' +
    '<video id="cam-video" playsinline muted style="width:100%;border-radius:10px;background:#000;aspect-ratio:4/3;object-fit:cover"></video>' +
    '<p id="cam-status" style="margin:8px 0;color:var(--mute)">Starting the camera...</p>' +
    '<div style="display:flex;justify-content:flex-end"><button type="button" class="btn" id="cam-done">Done</button></div>';
  document.body.appendChild(dlg);
  const video = dlg.querySelector('#cam-video'), status = dlg.querySelector('#cam-status');
  let stream = null, timer = null, detector = null, last = '', lastAt = 0;
  function stop() {
    clearInterval(timer); timer = null;
    if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
    video.srcObject = null;
  }
  async function start() {
    last = ''; lastAt = 0; status.textContent = 'Starting the camera...';
    try {
      stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: {ideal: 'environment'}}, audio: false});
      video.srcObject = stream; await video.play();
      const want = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf', 'qr_code', 'data_matrix'];
      let have = want;
      try { const sup = await BarcodeDetector.getSupportedFormats(); have = want.filter(f => sup.includes(f)); } catch (e) { /* use all */ }
      detector = new BarcodeDetector(have.length ? {formats: have} : undefined);
      status.textContent = 'Point the camera at the barcode.';
      timer = setInterval(tick, 250);
    } catch (e) {
      status.textContent = (e && e.name === 'NotAllowedError')
        ? 'Camera permission was denied. Allow the camera for this site in the browser settings, then try again.'
        : 'The camera could not be started on this device.';
    }
  }
  async function tick() {
    if (!detector || video.readyState < 2) return;
    let codes = [];
    try { codes = await detector.detect(video); } catch (e) { return; }
    if (!codes.length) return;
    const code = (codes[0].rawValue || '').trim(), now = Date.now();
    if (!code || (code === last && now - lastAt < 2500)) return;      // the same code in front of the camera is read once
    last = code; lastAt = now;
    if (navigator.vibrate) navigator.vibrate(60);
    input.value = code;
    input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}));
    status.textContent = 'Scanned ' + code + '. Scan the next one, or press Done.';
  }
  btn.addEventListener('click', () => { if (!dlg.open) dlg.showModal(); start(); });
  dlg.querySelector('#cam-done').addEventListener('click', () => dlg.close());
  dlg.addEventListener('close', stop);
  window.addEventListener('pagehide', stop);
})(document.getElementById('scan'));
