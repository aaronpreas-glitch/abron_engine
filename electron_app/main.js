const { app, BrowserWindow, Menu, ipcMain } = require('electron')
const path = require('path')

// ── Config ────────────────────────────────────────────────────────────────────
const DASHBOARD_URL = 'http://68.183.148.183:8000'

// ── Window ────────────────────────────────────────────────────────────────────
function createWindow() {
  const win = new BrowserWindow({
    width: 1600,
    height: 1000,
    minWidth: 1200,
    minHeight: 800,
    title: 'Abrons Engine',
    backgroundColor: '#080b14',
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 16, y: 16 },
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  })

  // Load dashboard
  win.loadURL(DASHBOARD_URL).catch(() => {
    // If the server is unreachable show a fallback page
    win.loadURL(`data:text/html,${encodeURIComponent(offlinePage())}`)
  })

  // Inject floating refresh button after every page load
  win.webContents.on('did-finish-load', () => {
    win.webContents.executeJavaScript(`
      (function() {
        if (document.getElementById('_ae_refresh')) return;
        const btn = document.createElement('button');
        btn.id = '_ae_refresh';
        btn.title = 'Refresh  (Cmd+R)';
        btn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';
        Object.assign(btn.style, {
          position: 'fixed',
          top: '4px',
          right: '14px',
          zIndex: '2147483647',
          width: '30px',
          height: '30px',
          borderRadius: '7px',
          background: 'rgba(255,255,255,0.07)',
          color: 'rgba(255,255,255,0.45)',
          border: '1px solid rgba(255,255,255,0.10)',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '0',
          transition: 'all 0.12s ease',
          backdropFilter: 'blur(8px)',
          WebkitAppRegion: 'no-drag',
        });
        btn.addEventListener('mouseenter', () => {
          btn.style.background = 'rgba(0,212,138,0.15)';
          btn.style.color = '#00d48a';
          btn.style.borderColor = 'rgba(0,212,138,0.3)';
          btn.style.boxShadow = '0 0 10px rgba(0,212,138,0.2)';
        });
        btn.addEventListener('mouseleave', () => {
          btn.style.background = 'rgba(255,255,255,0.07)';
          btn.style.color = 'rgba(255,255,255,0.45)';
          btn.style.borderColor = 'rgba(255,255,255,0.10)';
          btn.style.boxShadow = 'none';
        });
        btn.addEventListener('click', () => {
          btn.style.transform = 'rotate(360deg)';
          btn.style.transition = 'transform 0.35s ease, all 0.12s ease';
          setTimeout(() => {
            btn.style.transform = '';
            btn.style.transition = 'all 0.12s ease';
          }, 380);
          location.reload();
        });
        document.body.appendChild(btn);
      })();
    `).catch(() => {})
  })

  // Handle failed loads (VPS unreachable)
  win.webContents.on('did-fail-load', (_, errCode, errDesc) => {
    if (errCode === -3) return  // aborted (user navigated away), ignore
    win.loadURL(`data:text/html,${encodeURIComponent(offlinePage(errDesc))}`)
  })

  return win
}

// ── Offline fallback page ─────────────────────────────────────────────────────
function offlinePage(reason = 'Connection refused') {
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Abrons Engine — Offline</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #080b14;
    color: rgba(255,255,255,0.7);
    font-family: 'JetBrains Mono', 'Menlo', monospace;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    height: 100vh; gap: 16px;
    -webkit-app-region: drag;
  }
  .logo {
    width: 48px; height: 48px; border-radius: 14px;
    background: linear-gradient(135deg, #00d48a 0%, #006b48 100%);
    display: flex; align-items: center; justify-content: center;
    font-size: 22px; font-weight: 900; color: #000;
    box-shadow: 0 0 24px rgba(0,212,138,0.3);
  }
  h1 { font-size: 16px; font-weight: 700; color: rgba(255,255,255,0.9); letter-spacing: 0.06em; }
  p  { font-size: 11px; color: rgba(255,255,255,0.35); }
  .url { font-size: 10px; color: rgba(0,212,138,0.5); margin-top: 4px; }
  button {
    margin-top: 12px; padding: 8px 24px; border-radius: 6px;
    background: rgba(0,212,138,0.12); color: #00d48a;
    border: 1px solid rgba(0,212,138,0.3); cursor: pointer;
    font-family: inherit; font-size: 11px; font-weight: 700;
    letter-spacing: 0.06em; -webkit-app-region: no-drag;
  }
  button:hover { background: rgba(0,212,138,0.2); }
</style>
</head>
<body>
  <div class="logo">A</div>
  <h1>ABRONS ENGINE</h1>
  <p>Cannot reach the dashboard server</p>
  <p class="url">${DASHBOARD_URL}</p>
  <p style="font-size:10px;color:rgba(255,255,255,0.2);margin-top:4px">${reason}</p>
  <button onclick="location.reload()">⟳ Retry</button>
</body>
</html>`
}

// ── App menu ──────────────────────────────────────────────────────────────────
function buildMenu(win) {
  const template = [
    {
      label: 'Abrons Engine',
      submenu: [
        { label: 'About Abrons Engine', role: 'about' },
        { type: 'separator' },
        { label: 'Services', role: 'services' },
        { type: 'separator' },
        { label: 'Hide Abrons Engine', accelerator: 'Cmd+H', role: 'hide' },
        { label: 'Hide Others',        accelerator: 'Cmd+Alt+H', role: 'hideOthers' },
        { label: 'Show All',           role: 'unhide' },
        { type: 'separator' },
        { label: 'Quit Abrons Engine', accelerator: 'Cmd+Q', role: 'quit' },
      ],
    },
    {
      label: 'Edit',
      submenu: [
        { label: 'Undo',       role: 'undo',      accelerator: 'CmdOrCtrl+Z'       },
        { label: 'Redo',       role: 'redo',      accelerator: 'CmdOrCtrl+Shift+Z' },
        { type: 'separator' },
        { label: 'Cut',        role: 'cut',       accelerator: 'CmdOrCtrl+X'       },
        { label: 'Copy',       role: 'copy',      accelerator: 'CmdOrCtrl+C'       },
        { label: 'Paste',      role: 'paste',     accelerator: 'CmdOrCtrl+V'       },
        { type: 'separator' },
        { label: 'Select All', role: 'selectAll', accelerator: 'CmdOrCtrl+A'       },
      ],
    },
    {
      label: 'View',
      submenu: [
        {
          label: 'Refresh',
          accelerator: 'CmdOrCtrl+R',
          click: () => win.webContents.reload(),
        },
        {
          label: 'Force Refresh (clear cache)',
          accelerator: 'CmdOrCtrl+Shift+R',
          click: () => win.webContents.reloadIgnoringCache(),
        },
        { type: 'separator' },
        { label: 'Actual Size', role: 'resetZoom',        accelerator: 'CmdOrCtrl+0' },
        { label: 'Zoom In',     role: 'zoomIn',            accelerator: 'CmdOrCtrl+Plus' },
        { label: 'Zoom Out',    role: 'zoomOut',           accelerator: 'CmdOrCtrl+-' },
        { type: 'separator' },
        { label: 'Toggle Fullscreen', role: 'togglefullscreen', accelerator: 'Ctrl+Cmd+F' },
        { type: 'separator' },
        { label: 'Developer Tools', accelerator: 'Alt+Cmd+I', role: 'toggleDevTools' },
      ],
    },
    {
      label: 'Window',
      submenu: [
        { label: 'Minimize',           role: 'minimize', accelerator: 'Cmd+M' },
        { label: 'Zoom',               role: 'zoom' },
        { type: 'separator' },
        { label: 'Bring All to Front', role: 'front' },
      ],
    },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────
app.whenReady().then(() => {
  const win = createWindow()
  buildMenu(win)

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      const w = createWindow()
      buildMenu(w)
    }
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
