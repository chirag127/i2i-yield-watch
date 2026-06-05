// scraper/logger.js
// Structured JSON logs — GitHub Actions can filter by
// level, runId, and phase. Human-readable fallback
// when LOG_FORMAT=text.

let runId = null;

function formatLog(level, msg, extra = {}) {
  if (process.env.LOG_FORMAT === 'text') {
    const prefix = `[${level.toUpperCase()}]  ${
      new Date().toISOString()}`;
    const suffix = runId ? ` [${runId}]` : '';
    const line = `${prefix}${suffix} ${msg}`;
    if (level === 'error') console.error(line);
    else if (level === 'warn') console.warn(line);
    else console.log(line);
    return;
  }

  const entry = {
    level,
    ts: new Date().toISOString(),
    msg,
    ...(runId ? { runId } : {}),
    ...extra,
  };
  const line = JSON.stringify(entry);
  if (level === 'error') console.error(line);
  else if (level === 'warn') console.warn(line);
  else console.log(line);
}

const logger = {
  setRunId: (id) => {
    runId = id;
  },
  info: (msg, extra) => formatLog('info', msg, extra),
  warn: (msg, extra) => formatLog('warn', msg, extra),
  error: (msg, extra) => formatLog('error', msg, extra),
  phase: (name, durationMs, extra = {}) => {
    formatLog('info', `phase:${name}`, {
      phase: name,
      duration_ms: durationMs,
      ...extra,
    });
  },
};

module.exports = logger;
