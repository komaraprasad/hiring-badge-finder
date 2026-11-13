const DEFAULT_SERVER = 'http://localhost:8000';
// Filled in when the extension is downloaded from a deployed dashboard (its own address only).
const DEPLOYED_SERVER = '';
const input = document.getElementById('server');
const msg = document.getElementById('msg');

function allowed(u) {
  try {
    const url = new URL(u);
    if (url.protocol === 'http:' && (url.hostname === 'localhost' || url.hostname === '127.0.0.1')) return true;
    if (DEPLOYED_SERVER && url.origin === new URL(DEPLOYED_SERVER).origin) return true;
  } catch (e) { /* not a URL */ }
  return false;
}

chrome.storage.sync.get('serverUrl').then(({ serverUrl }) => {
  input.value = serverUrl || DEFAULT_SERVER;
});

document.getElementById('save').addEventListener('click', async () => {
  const value = input.value.trim().replace(/\/+$/, '') || DEFAULT_SERVER;
  if (!allowed(value)) {
    msg.className = 'bad';
    msg.textContent = 'Use http://localhost:8000' + (DEPLOYED_SERVER ? ' or ' + DEPLOYED_SERVER : '') + '.';
    return;
  }
  await chrome.storage.sync.set({ serverUrl: value });
  msg.className = 'ok';
  msg.textContent = 'Saved.';
});
