// preload.js — runs in renderer context before the page loads.
// contextIsolation: true means this file CAN access Node/Electron APIs
// but they are NOT exposed to the dashboard page unless explicitly bridged.
// No bridge needed here — the dashboard is a self-contained React app.

// Nothing to expose. File is required by Electron's security model.
