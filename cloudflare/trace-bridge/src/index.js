const DEFAULT_EVENT_TYPE = "agent_trace";
const DEFAULT_MAX_AGENT_ID_LEN = 128;
const DEFAULT_MAX_MESSAGE_LEN = 280;
const DEFAULT_MAX_TRACE_ID_LEN = 128;
const DEFAULT_MAX_SOURCE_LEN = 64;
const DEFAULT_MAX_PAGE_URL_LEN = 512;
const DEFAULT_MAX_USER_AGENT_LEN = 512;

function asInt(value, fallback) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return parsed;
}

function clean(value) {
  return String(value ?? "").trim();
}

function json(status, body, cors = {}) {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    ...cors,
  });
  return new Response(JSON.stringify(body), { status, headers });
}

function allowedOrigins(env) {
  return String(env.ALLOWED_ORIGINS ?? "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

function corsHeaders(origin, env) {
  const configured = allowedOrigins(env);
  if (!origin) return {};
  if (configured.length === 0) {
    return {
      "Access-Control-Allow-Origin": origin,
      Vary: "Origin",
    };
  }
  if (!configured.includes(origin)) return null;
  return {
    "Access-Control-Allow-Origin": origin,
    Vary: "Origin",
  };
}

function parseRepo(rawRepo) {
  const repo = clean(rawRepo);
  const parts = repo.split("/");
  if (parts.length !== 2) return null;
  const owner = clean(parts[0]);
  const name = clean(parts[1]);
  if (!owner || !name) return null;
  return { owner, name, repo: `${owner}/${name}` };
}

function validatePayload(input, env) {
  const maxAgentId = asInt(env.MAX_AGENT_ID_LENGTH, DEFAULT_MAX_AGENT_ID_LEN);
  const maxMessage = asInt(env.MAX_MESSAGE_LENGTH, DEFAULT_MAX_MESSAGE_LEN);
  const maxTraceId = asInt(env.MAX_TRACE_ID_LENGTH, DEFAULT_MAX_TRACE_ID_LEN);
  const maxSource = asInt(env.MAX_SOURCE_LENGTH, DEFAULT_MAX_SOURCE_LEN);
  const maxPageUrl = asInt(env.MAX_PAGE_URL_LENGTH, DEFAULT_MAX_PAGE_URL_LEN);
  const maxUserAgent = asInt(env.MAX_USER_AGENT_LENGTH, DEFAULT_MAX_USER_AGENT_LEN);

  const agent_id = clean(input?.agent_id);
  const message = clean(input?.message);
  const trace_id = clean(input?.trace_id);
  const source = clean(input?.source || "browser-state");
  const page_url = clean(input?.page_url);
  const user_agent = clean(input?.user_agent);

  if (!agent_id || agent_id.length > maxAgentId) return { ok: false, reason: "invalid_agent_id" };
  if (!message || message.length > maxMessage) return { ok: false, reason: "invalid_message" };
  if (!trace_id || trace_id.length > maxTraceId) return { ok: false, reason: "invalid_trace_id" };
  if (!source || source.length > maxSource) return { ok: false, reason: "invalid_source" };
  if (page_url.length > maxPageUrl) return { ok: false, reason: "invalid_page_url" };
  if (user_agent.length > maxUserAgent) return { ok: false, reason: "invalid_user_agent" };

  return {
    ok: true,
    payload: {
      agent_id,
      message,
      trace_id,
      source,
      page_url,
      user_agent,
    },
  };
}

async function dispatchToGitHub(env, payload) {
  const token = clean(env.GITHUB_TOKEN || env.LOOM_GITHUB_TOKEN);
  if (!token) return { ok: false, status: 503, reason: "missing_github_token" };

  const repo = parseRepo(env.LOOM_GITHUB_REPO);
  if (!repo) return { ok: false, status: 503, reason: "misconfigured_repo" };

  const eventType = clean(env.LOOM_GITHUB_EVENT_TYPE) || DEFAULT_EVENT_TYPE;
  const endpoint = `https://api.github.com/repos/${encodeURIComponent(repo.owner)}/${encodeURIComponent(repo.name)}/dispatches`;

  const res = await fetch(endpoint, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "loom-trace-bridge/1.0",
    },
    body: JSON.stringify({
      event_type: eventType,
      client_payload: payload,
    }),
  });

  if (res.ok) {
    return { ok: true, status: 202, eventType };
  }

  const text = (await res.text()) || "";
  return {
    ok: false,
    status: res.status,
    reason: `github_dispatch_failed_${res.status}`,
    detail: text.slice(0, 500),
  };
}

function preflightResponse(origin, env) {
  const cors = corsHeaders(origin, env);
  if (cors === null) {
    return json(403, { error: "origin_not_allowed" });
  }
  return new Response(null, {
    status: 204,
    headers: {
      ...cors,
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "86400",
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get("Origin") || "";
    const cors = corsHeaders(origin, env);

    if (request.method === "OPTIONS") {
      return preflightResponse(origin, env);
    }

    if (url.pathname === "/health") {
      if (cors === null) return json(403, { error: "origin_not_allowed" });
      return json(200, { status: "ok" }, cors || {});
    }

    if (url.pathname !== "/api/trace") {
      if (cors === null) return json(403, { error: "origin_not_allowed" });
      return json(404, { error: "not_found" }, cors || {});
    }

    if (cors === null) {
      return json(403, { error: "origin_not_allowed" });
    }

    if (request.method !== "POST") {
      return json(405, { error: "method_not_allowed" }, cors || {});
    }

    const contentType = clean(request.headers.get("content-type")).toLowerCase();
    if (!contentType.includes("application/json")) {
      return json(415, { error: "unsupported_media_type" }, cors || {});
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return json(400, { error: "invalid_json" }, cors || {});
    }

    const validated = validatePayload(body, env);
    if (!validated.ok) {
      return json(400, { error: validated.reason }, cors || {});
    }

    const dispatched = await dispatchToGitHub(env, validated.payload);
    if (!dispatched.ok) {
      return json(
        dispatched.status || 502,
        {
          error: dispatched.reason || "dispatch_failed",
          detail: dispatched.detail || "",
        },
        cors || {},
      );
    }

    return json(
      202,
      {
        status: "accepted",
        queued: true,
        event_type: dispatched.eventType,
        trace_id: validated.payload.trace_id,
      },
      cors || {},
    );
  },
};
