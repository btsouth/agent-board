#!/usr/bin/env node

import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { DatabaseSync } from "node:sqlite";

const VERSION = "0.1.0";
const HEARTBEAT_MS = 20_000;
const PUBLISH_MS = 20_000;
const RECONNECT_MS = 2_000;
const SHELL_RECONCILE_MS = 2_000;

const HOME = os.homedir();
const DEFAULT_T3_HOME = path.join(HOME, ".t3");
const CONFIG_DIR = path.join(process.env.XDG_CONFIG_HOME || path.join(HOME, ".config"), "agent-board");
const STATE_DIR = path.join(process.env.XDG_STATE_HOME || path.join(HOME, ".local", "state"), "agent-board");
const TOKEN_PATH = path.join(CONFIG_DIR, "t3-session-token");
const STATE_PATH = path.join(STATE_DIR, "t3-provider-state.json");

const args = process.argv.slice(2);
const argValue = (name) => {
  const inline = args.find((arg) => arg.startsWith(`${name}=`));
  if (inline) return inline.slice(name.length + 1);
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : undefined;
};

if (!args.includes("--jsonl")) {
  console.error("usage: agent-board-t3-provider --jsonl [--t3-home PATH]");
  process.exit(2);
}

const t3Home = path.resolve(argValue("--t3-home") || process.env.T3CODE_HOME || DEFAULT_T3_HOME);
const runtimePath = path.join(t3Home, "userdata", "server-runtime.json");
const databasePath = path.join(t3Home, "userdata", "state.sqlite");
const logPath = path.join(STATE_DIR, "t3-provider-node.log");

fs.mkdirSync(STATE_DIR, { recursive: true, mode: 0o700 });
fs.mkdirSync(CONFIG_DIR, { recursive: true, mode: 0o700 });

const logStream = fs.createWriteStream(logPath, { flags: "a", mode: 0o600 });

function log(message, detail) {
  const suffix = detail === undefined ? "" : ` ${typeof detail === "string" ? detail : JSON.stringify(detail)}`;
  logStream.write(`${new Date().toISOString()} ${message}${suffix}\n`);
  if (process.env.AGENT_BOARD_T3_VERBOSE === "1") console.log(message, detail ?? "");
}

function readJson(filePath, fallback = null) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

async function writeJsonAtomic(filePath, value) {
  const temp = `${filePath}.${process.pid}.tmp`;
  await fsp.writeFile(temp, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  await fsp.rename(temp, filePath);
}

function truncate(value, length) {
  const text = String(value ?? "");
  return text.length <= length ? text : `${text.slice(0, Math.max(0, length - 1))}…`;
}

function firstLine(value, length = 72) {
  const line = String(value ?? "")
    .replace(/\s+/g, " ")
    .trim();
  return truncate(line || "New agent session", length);
}

function readToken() {
  try {
    return fs.readFileSync(TOKEN_PATH, "utf8").trim();
  } catch {
    return "";
  }
}

function writeToken(token) {
  fs.writeFileSync(TOKEN_PATH, `${token}\n`, { encoding: "utf8", mode: 0o600 });
  fs.chmodSync(TOKEN_PATH, 0o600);
}

function readRuntime() {
  const runtime = readJson(runtimePath);
  const port = Number(runtime?.port);
  if (!runtime || !Number.isInteger(port) || port <= 0) {
    throw new Error("T3 Code server is not running.");
  }
  const host = !runtime.host || runtime.host === "0.0.0.0" || runtime.host === "::" ? "127.0.0.1" : runtime.host;
  const httpBaseUrl = String(runtime.origin || `http://${host}:${port}`).replace(/\/$/, "");
  return {
    httpBaseUrl,
    wsBaseUrl: httpBaseUrl.replace(/^http:/, "ws:").replace(/^https:/, "wss:"),
  };
}

function findRunningT3Command() {
  for (const entry of fs.readdirSync("/proc")) {
    if (!/^\d+$/.test(entry)) continue;
    let commandArgs;
    try {
      commandArgs = fs
        .readFileSync(path.join("/proc", entry, "cmdline"))
        .toString("utf8")
        .split("\0")
        .filter(Boolean);
    } catch {
      continue;
    }
    const serverBin = commandArgs.find((arg) => arg.endsWith("/apps/server/dist/bin.mjs"));
    if (serverBin && commandArgs[0]) return { executable: commandArgs[0], serverBin };
  }
  return null;
}

function issueToken() {
  const command = findRunningT3Command();
  if (!command) throw new Error("Could not find the running T3 Code server process.");
  return new Promise((resolve, reject) => {
    const child = spawn(
      command.executable,
      [
        command.serverBin,
        "auth",
        "session",
        "issue",
        "--token-only",
        "--ttl",
        "30d",
        "--label",
        "T3 Code in-game bridge",
      ],
      {
        env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", reject);
    child.on("close", (code) => {
      const token = stdout.trim().split(/\s+/).at(-1) || "";
      if (code !== 0 || token.length < 100) {
        reject(new Error(stderr.trim() || `T3 token issuance exited ${code}.`));
      } else {
        resolve(token);
      }
    });
  });
}

async function fetchJson(url, options = {}, timeoutMs = 8_000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error(body?.message || `${url} returned ${response.status}`);
    return body;
  } finally {
    clearTimeout(timer);
  }
}

async function validateToken(endpoint, token) {
  if (!token) return false;
  try {
    const session = await fetchJson(`${endpoint.httpBaseUrl}/api/auth/session`, {
      headers: { authorization: `Bearer ${token}` },
    });
    return session?.authenticated === true;
  } catch {
    return false;
  }
}

async function resolveToken(endpoint) {
  const existing = readToken();
  if (await validateToken(endpoint, existing)) return existing;
  const issued = await issueToken();
  if (!(await validateToken(endpoint, issued))) throw new Error("T3 rejected the newly issued bridge token.");
  writeToken(issued);
  return issued;
}

async function issueWebSocketTicket(endpoint, token) {
  const response = await fetchJson(`${endpoint.httpBaseUrl}/api/auth/websocket-ticket`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
    },
    body: "{}",
  });
  if (!response?.ticket) throw new Error("T3 did not return a WebSocket ticket.");
  return response.ticket;
}

class T3RpcClient {
  constructor(endpoint, token) {
    this.endpoint = endpoint;
    this.token = token;
    this.socket = null;
    this.nextId = 1;
    this.pending = new Map();
    this.streams = new Map();
    this.closed = false;
    this.heartbeat = null;
  }

  async connect() {
    const ticket = await issueWebSocketTicket(this.endpoint, this.token);
    const url = new URL(this.endpoint.wsBaseUrl);
    url.pathname = "/ws";
    url.searchParams.set("wsTicket", ticket);
    url.searchParams.set("clientSurface", "desktop");
    url.searchParams.set("clientAppVersion", `agent-board-t3-provider/${VERSION}`);
    this.socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      const onOpen = () => {
        cleanup();
        resolve();
      };
      const onError = (event) => {
        cleanup();
        reject(event.error || new Error("T3 WebSocket connection failed."));
      };
      const onClose = (event) => {
        cleanup();
        reject(new Error(`T3 WebSocket closed before opening (${event.code}).`));
      };
      const cleanup = () => {
        this.socket?.removeEventListener("open", onOpen);
        this.socket?.removeEventListener("error", onError);
        this.socket?.removeEventListener("close", onClose);
      };
      this.socket.addEventListener("open", onOpen, { once: true });
      this.socket.addEventListener("error", onError, { once: true });
      this.socket.addEventListener("close", onClose, { once: true });
    });
    this.socket.addEventListener("message", (event) => this.handleMessage(event.data));
    this.socket.addEventListener("close", () => {
      for (const { reject } of this.pending.values()) reject(new Error("T3 WebSocket closed."));
      this.pending.clear();
      this.streams.clear();
      if (!this.closed) log("T3 WebSocket closed");
    });
    this.socket.addEventListener("error", (event) => log("T3 WebSocket error", event.message || String(event)));
    this.heartbeat = setInterval(() => this.send({ _tag: "Ping" }), HEARTBEAT_MS);
    this.heartbeat.unref?.();
  }

  close() {
    this.closed = true;
    clearInterval(this.heartbeat);
    this.socket?.close();
    this.socket = null;
  }

  send(message) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      throw new Error("T3 WebSocket is not connected.");
    }
    this.socket.send(JSON.stringify(message));
  }

  request(tag, payload) {
    const id = String(this.nextId++);
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      try {
        this.send({ _tag: "Request", id, tag, payload, headers: [] });
      } catch (error) {
        this.pending.delete(id);
        reject(error);
      }
    });
  }

  stream(tag, payload, onValues, onClose) {
    const id = String(this.nextId++);
    this.streams.set(id, { onValues, onClose });
    this.send({ _tag: "Request", id, tag, payload, headers: [] });
    return id;
  }

  interrupt(requestId) {
    if (requestId) this.send({ _tag: "Interrupt", requestId: String(requestId), interruptors: [] });
  }

  handleMessage(raw) {
    let message;
    try {
      message = JSON.parse(String(raw));
    } catch {
      log("Ignored malformed T3 RPC message");
      return;
    }
    if (message._tag === "Pong") return;
    if (message._tag === "Chunk") {
      const stream = this.streams.get(String(message.requestId));
      if (stream && Array.isArray(message.values)) {
        try {
          stream.onValues(message.values);
        } catch (error) {
          log("T3 stream handler failed", error instanceof Error ? error.message : String(error));
        }
      }
      return;
    }
    if (message._tag === "Exit") {
      const id = String(message.requestId);
      const pending = this.pending.get(id);
      if (pending) {
        this.pending.delete(id);
        if (message.exit?._tag === "Success") pending.resolve(message.exit.value);
        else pending.reject(message.exit?.cause || message.exit || new Error("T3 RPC failed."));
      }
      const stream = this.streams.get(id);
      if (stream) {
        this.streams.delete(id);
        stream.onClose?.(message.exit);
      }
      return;
    }
    if (message._tag === "Defect") {
      log("T3 RPC defect", message.defect);
    }
  }
}

class Bridge {
  constructor() {
    this.state = {
      seen: {},
      lastStatus: {},
      seeded: false,
      ...readJson(STATE_PATH, {}),
    };
    this.endpoint = null;
    this.token = "";
    this.client = null;
    this.shellRequestId = null;
    this.shell = null;
    this.projects = new Map();
    this.threads = new Map();
    this.database = null;
    this.statements = null;
    this.lastPublishAt = 0;
    this.lastReconcileAt = 0;
    this.latestNotice = "";
    this.connected = false;
    this.shellReady = false;
    this.stopping = false;
    this.dirty = true;
  }

  async start() {
    process.on("SIGINT", () => this.stop());
    process.on("SIGTERM", () => this.stop());
    this.startJsonlInput();
    await this.connectLoop();
  }

  async stop() {
    this.stopping = true;
    this.client?.close();
    await this.saveState();
    logStream.end();
  }

  async saveState() {
    await writeJsonAtomic(STATE_PATH, this.state);
  }

  async connectLoop() {
    let reconnectDelay = RECONNECT_MS;
    while (!this.stopping) {
      try {
        this.endpoint = readRuntime();
        this.token = await resolveToken(this.endpoint);
        this.client = new T3RpcClient(this.endpoint, this.token);
        await this.client.connect();
        this.connected = true;
        this.shellReady = false;
        reconnectDelay = RECONNECT_MS;
        this.dirty = true;
        this.latestNotice = "";
        log("Connected to T3", this.endpoint.httpBaseUrl);
        this.shellRequestId = this.client.stream(
          "orchestration.subscribeShell",
          { requestCompletionMarker: true },
          (items) => this.onShellItems(items),
          () => {
            this.connected = false;
          },
        );
        await this.runConnectedLoop();
      } catch (error) {
        this.connected = false;
        this.latestNotice = `T3 unavailable: ${error instanceof Error ? error.message : String(error)}`;
        log("Bridge connection failed", this.latestNotice);
        await this.publish();
        await sleep(reconnectDelay);
        reconnectDelay = Math.min(30_000, reconnectDelay * 2);
      }
    }
  }

  async runConnectedLoop() {
    while (!this.stopping && this.client?.socket?.readyState === WebSocket.OPEN) {
      if (Date.now() - this.lastReconcileAt >= SHELL_RECONCILE_MS) {
        this.lastReconcileAt = Date.now();
        await this.reconcileShell().catch((error) => {
          log("Could not reconcile T3 shell", error instanceof Error ? error.message : String(error));
        });
      }
      if (this.dirty || Date.now() - this.lastPublishAt >= PUBLISH_MS) await this.publish();
      await sleep(100);
    }
    this.connected = false;
    this.client?.close();
    await sleep(RECONNECT_MS);
  }

  async reconcileShell() {
    const snapshot = await fetchJson(`${this.endpoint.httpBaseUrl}/api/orchestration/shell`, {
      headers: { authorization: `Bearer ${this.token}` },
    });
    if (!snapshot || !Array.isArray(snapshot.projects) || !Array.isArray(snapshot.threads)) {
      throw new Error("T3 shell snapshot was malformed.");
    }
    this.applyShellSnapshot(snapshot);
  }

  startJsonlInput() {
    process.stdin.setEncoding("utf8");
    let buffer = "";
    process.stdin.on("data", (chunk) => {
      buffer += chunk;
      while (true) {
        const newline = buffer.indexOf("\n");
        if (newline < 0) break;
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (line) void this.handleJsonlInput(line);
      }
    });
  }

  async handleJsonlInput(line) {
    let request;
    try {
      request = JSON.parse(line);
      const action = request.action || {};
      const result = await this.dispatch({
        seq: request.id,
        kind: action.kind,
        provider: "t3",
        host: "t3",
        sessionId: action.session_id || action.sessionId || "",
        text: action.text || "",
        workspaceRoot: action.workspace_root || action.workspaceRoot || "",
        title: action.title || "",
        answers: action.answers,
        requestId: action.request_id,
      });
      process.stdout.write(`${JSON.stringify({ type: "result", id: request.id, ...result })}\n`);
      await this.publish();
    } catch (error) {
      process.stdout.write(
        `${JSON.stringify({
          type: "result",
          id: request?.id ?? null,
          ok: false,
          message: error instanceof Error ? error.message : String(error),
        })}\n`,
      );
    }
  }

  onShellItems(items) {
    this.dirty = true;
    for (const item of items) {
      if (item.kind === "snapshot") {
        this.shellReady = true;
        this.applyShellSnapshot(item.snapshot);
      } else if (item.kind === "project-upserted" && item.project) {
        this.projects.set(item.project.id, item.project);
      } else if (item.kind === "project-removed") {
        this.projects.delete(item.projectId);
      } else if (item.kind === "thread-upserted" && item.thread) {
        this.threads.set(item.thread.id, item.thread);
      } else if (item.kind === "thread-removed") {
        this.threads.delete(item.threadId);
      }
    }
  }

  applyShellSnapshot(snapshot) {
    this.shell = snapshot;
    this.projects = new Map(snapshot.projects.map((project) => [project.id, project]));
    this.threads = new Map(snapshot.threads.map((thread) => [thread.id, thread]));
    this.dirty = true;
    if (!this.state.seeded) {
      const now = Date.now();
      for (const thread of snapshot.threads) this.state.seen[thread.id] = now;
      this.state.seeded = true;
      void this.saveState();
    }
  }

  openDatabase() {
    if (this.database || !fs.existsSync(databasePath)) return;
    try {
      this.database = new DatabaseSync(databasePath, { readOnly: true });
      this.statements = {
        messageCount: this.database.prepare(
          "SELECT COUNT(*) AS count FROM projection_thread_messages WHERE thread_id = ? AND role IN ('user', 'assistant')",
        ),
        latestAssistant: this.database.prepare(
          "SELECT text, created_at FROM projection_thread_messages WHERE thread_id = ? AND role = 'assistant' ORDER BY created_at DESC LIMIT 1",
        ),
        latestActivity: this.database.prepare(
          "SELECT summary FROM projection_thread_activities WHERE thread_id = ? ORDER BY created_at DESC, sequence DESC LIMIT 1",
        ),
        pendingApproval: this.database.prepare(
          "SELECT request_id FROM projection_pending_approvals WHERE thread_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
        ),
        approvalActivity: this.database.prepare(
          "SELECT summary, payload_json FROM projection_thread_activities WHERE thread_id = ? AND tone = 'approval' ORDER BY created_at DESC, sequence DESC LIMIT 1",
        ),
        latestUserInput: this.database.prepare(
          "SELECT kind, payload_json, created_at FROM projection_thread_activities WHERE thread_id = ? AND kind IN ('user-input.requested', 'user-input.resolved') ORDER BY created_at DESC, sequence DESC LIMIT 1",
        ),
        // Completed tool calls, reduced in SQL to the few fields a one-line
        // step needs: the payloads can carry whole files and base64 images.
        recentTools: this.database.prepare(
          `SELECT activity_id, created_at,
             json_extract(payload_json, '$.itemType') AS item_type,
             json_extract(payload_json, '$.data.toolName') AS tool_name,
             substr(coalesce(json_extract(payload_json, '$.data.input.command'),
                             json_extract(payload_json, '$.data.input.file_path'),
                             json_extract(payload_json, '$.data.input.query'),
                             json_extract(payload_json, '$.data.input.description'),
                             json_extract(payload_json, '$.detail'), ''), 1, 400) AS subject
           FROM projection_thread_activities
           WHERE thread_id = ? AND kind = 'tool.completed'
           ORDER BY created_at DESC, sequence DESC LIMIT 60`,
        ),
        recentMessages: this.database.prepare(
          // Reasoning rows share this table; T3 hides them in its own transcript.
          "SELECT message_id, role, text, created_at FROM projection_thread_messages WHERE thread_id = ? AND role IN ('user', 'assistant') ORDER BY created_at DESC LIMIT 60",
        ),
      };
    } catch (error) {
      log("Could not open T3 database read-only", error instanceof Error ? error.message : String(error));
      this.database = null;
      this.statements = null;
    }
  }

  query(statement, ...params) {
    this.openDatabase();
    if (!statement || !this.statements?.[statement]) return undefined;
    try {
      return this.statements[statement].get(...params);
    } catch (error) {
      log("T3 database query failed", { statement, error: String(error) });
      return undefined;
    }
  }

  // Tool steps only change when the thread does, so they are cached on its
  // update time rather than re-read for every thread on every publish.
  toolSteps(thread) {
    this.toolCache ||= new Map();
    const key = String(thread.updatedAt || "");
    const cached = this.toolCache.get(thread.id);
    if (cached && cached.key === key) return cached.steps;
    const steps = this.queryAll("recentTools", thread.id)
      .reverse()
      .map((row) => ({
        id: row.activity_id,
        role: "tool",
        text: stepLabel(row.item_type, row.tool_name, row.subject),
        created_at: String(row.created_at || ""),
      }));
    this.toolCache.set(thread.id, { key, steps });
    return steps;
  }

  queryAll(statement, ...params) {
    this.openDatabase();
    if (!statement || !this.statements?.[statement]) return [];
    try {
      return this.statements[statement].all(...params);
    } catch (error) {
      log("T3 database query failed", { statement, error: String(error) });
      return [];
    }
  }

  threadView(thread) {
    const session = thread.session;
    const latestTurn = thread.latestTurn;
    const pendingApproval = this.query("pendingApproval", thread.id);
    const latestActivity = this.query("latestActivity", thread.id);
    const approvalActivity = this.query("approvalActivity", thread.id);
    const userInputActivity = this.query("latestUserInput", thread.id);
    const latestAssistant = this.query("latestAssistant", thread.id);
    const recentMessages = this.queryAll("recentMessages", thread.id).reverse();
    const steps = this.toolSteps(thread);
    const messageCount = Number(this.query("messageCount", thread.id)?.count || 0);
    const updatedMs = safeTime(thread.updatedAt);
    // Seen means opened here, or settled in T3 itself after the last reply: a
    // thread you already dealt with in T3 is not news on the board.
    const seenMs = Math.max(Number(this.state.seen[thread.id] || 0), safeTime(thread.settledAt) || 0);
    const failed = session?.status === "error" || latestTurn?.state === "error";
    const replyMs = safeTime(latestAssistant?.created_at) || 0;
    const signalMs = failed ? Math.max(replyMs, updatedMs || 0) : replyMs;
    const unread = Boolean(signalMs && signalMs > seenMs && latestTurn?.state !== "running");
    const hasApproval = thread.hasPendingApprovals === true && Boolean(pendingApproval?.request_id);
    const hasUserInput = thread.hasPendingUserInput === true;
    let userInput = null;
    if (hasUserInput && userInputActivity?.kind === "user-input.requested") {
      try {
        const payload = JSON.parse(userInputActivity.payload_json || "{}");
        const question = Array.isArray(payload.questions) ? payload.questions[0] : null;
        if (payload.requestId && question?.id) {
          const options = Array.isArray(question.options)
            ? question.options.map((option) => option.label).filter(Boolean)
            : [];
          userInput = {
            requestId: String(payload.requestId),
            questionId: String(question.id),
            summary: String(question.question || question.header || "Agent needs an answer"),
            options: options.join(" ~ "),
            questions: payload.questions,
          };
        }
      } catch (error) {
        log("Could not parse pending user input", error instanceof Error ? error.message : String(error));
      }
    }

    let status = "idle";
    if (hasApproval || hasUserInput) status = "needs";
    else if (session?.status === "error" || latestTurn?.state === "error") status = "error";
    else if (session?.status === "running" || session?.status === "starting" || latestTurn?.state === "running" || thread.backgroundLiveness === "working") status = "working";
    else if (session?.status === "starting" || thread.backgroundLiveness === "monitoring") status = "waiting";
    else if (unread) status = "reply";
    else if (latestTurn?.state === "completed" || session?.status === "stopped" || session?.status === "interrupted") status = "finished";

    let activity = latestActivity?.summary || "";
    if (hasApproval) activity = approvalActivity?.summary || "Approval required";
    else if (hasUserInput) activity = userInput?.summary || "Agent needs an answer";
    else if (session?.status === "running") activity = thread.planProgress?.step || "Working";
    else if (session?.status === "starting") activity = "Starting";
    else if (failed) activity = session?.lastError || errorLine(latestAssistant?.text) || "Turn failed";
    else if (latestTurn?.state === "completed") activity = "Turn complete";

    const project = this.projects.get(thread.projectId);
    const model = thread.modelSelection?.model || project?.defaultModelSelection?.model || "";
    const instance = thread.modelSelection?.instanceId || session?.providerInstanceId || project?.defaultModelSelection?.instanceId || "";
    const preview = recentMessages.length
      ? recentMessages
          .slice(-4)
          .map((message) => `${message.role === "user" ? "You" : "T3"}: ${message.text}`)
          .join(" ~ ")
      : latestAssistant?.text || activity || thread.title;

    return {
      thread,
      project,
      status,
      unread,
      snippet: snippetLine(latestAssistant?.text),
      age: Math.max(0, Math.floor((Date.now() - (updatedMs || Date.now())) / 1000)),
      activity,
      preview,
      messageCount,
      profile: [instance, model].filter(Boolean).join(" / "),
      activityAt: Math.floor(updatedMs / 1000),
      approvalRequestId: hasApproval ? pendingApproval.request_id : "",
      approvalSummary: approvalActivity?.summary || (hasApproval ? "Approval required" : ""),
      userInputRequestId: userInput?.requestId || "",
      userInputQuestionId: userInput?.questionId || "",
      userInputSummary: userInput?.summary || (hasUserInput ? "Agent needs an input answer" : ""),
      userInputOptions: userInput?.options || "",
      userInputQuestions: userInput?.questions || [],
      conversation: interleave(
        recentMessages.map((message) => ({
          id: message.message_id,
          role: message.role === "user" ? "user" : "agent",
          text: String(message.text || ""),
          created_at: String(message.created_at || ""),
        })),
        steps,
      ),
    };
  }

  async publish() {
    this.lastPublishAt = Date.now();
    let enriched = [];
    try {
      enriched = [...this.threads.values()]
        .filter((thread) => !thread.deletedAt && !thread.archivedAt)
        .map((thread) => this.threadView(thread));
    } catch (error) {
      this.latestNotice = `Bridge publish failed: ${error instanceof Error ? error.message : String(error)}`;
    }

    if (this.connected && !this.shellReady) return;
    const statusLabels = {
      needs: "Needs you",
      error: "Error",
      working: "Working",
      waiting: "Waiting",
      reply: "New reply",
      idle: "Idle",
      finished: "Finished",
    };
    process.stdout.write(
      `${JSON.stringify({
        type: "snapshot",
        connected: this.connected,
        notice: this.latestNotice,
        projects: [...this.projects.values()].map((project) => ({
          id: project.id,
          title: project.title,
          workspace_root: project.workspaceRoot || "",
          default_model_selection: project.defaultModelSelection || null,
        })),
        rows: enriched.map((item) => ({
          id: item.thread.id,
          provider: "t3",
          provider_label: "T3 Code",
          host: "local",
          title: item.thread.title,
          project: item.project?.title || "Project",
          profile: item.profile,
          status: item.status,
          status_label: statusLabels[item.status] || item.status,
          unread: item.unread,
          snippet: item.snippet,
          settled: Boolean(item.thread.settledAt),
          age_s: item.age,
          activity_at: item.activityAt,
          activity: item.activity,
          messages: item.messageCount,
          cost_usd: 0,
          preview: item.preview,
          approval_request_id: item.approvalRequestId,
          approval_summary: item.approvalSummary,
          user_input_request_id: item.userInputRequestId,
          user_input_question_id: item.userInputQuestionId,
          user_input_summary: item.userInputSummary,
          user_input_options: item.userInputOptions,
          user_input_questions: item.userInputQuestions,
          conversation: item.conversation,
          capabilities: ["reply", "new", "approve", "decline", "answer", "focus", "mark_read", "stop", "settle"],
        })),
      })}\n`,
    );
    this.dirty = false;
    await this.saveState();
  }

  async dispatch(action) {
    const now = new Date().toISOString();
    if (action.kind === "mark_read") {
      const stamp = Number(action.text) || Math.floor(Date.now() / 1000);
      this.state.seen[action.sessionId] = Math.max(stamp * 1000, Number(this.state.seen[action.sessionId] || 0));
      this.dirty = true;
      return { ok: true, message: `Marked ${action.sessionId} read.` };
    }
    if (action.kind === "reply") {
      const thread = this.threads.get(action.sessionId);
      const messageId = randomUUID();
      if (!thread) throw new Error("Session is no longer available; message retained.");
      await this.client.request("orchestration.dispatchCommand", {
        type: "thread.turn.start",
        commandId: randomUUID(),
        threadId: action.sessionId,
        message: {
          messageId,
          role: "user",
          text: action.text,
          attachments: [],
        },
        runtimeMode: thread.runtimeMode || "full-access",
        interactionMode: thread.interactionMode || "default",
        createdAt: now,
      });
      return { ok: true, message: `Delivered reply to ${action.sessionId}.`, messageId };
    }
    // Settle is T3's "done with this thread": it leaves the active list but
    // stays in history. Archive is a different, stronger thing in T3 and is
    // only sent when asked for by name.
    if (action.kind === "settle" || action.kind === "unsettle" || action.kind === "archive") {
      if (!this.threads.has(action.sessionId)) throw new Error("Session is no longer available.");
      await this.client.request("orchestration.dispatchCommand", {
        type: `thread.${action.kind}`,
        commandId: randomUUID(),
        threadId: action.sessionId,
        ...(action.kind === "unsettle" ? { reason: "user" } : {}),
      });
      this.dirty = true;
      const done = { settle: "Settled", unsettle: "Moved back to active", archive: "Archived" }[action.kind];
      return { ok: true, message: `${done} ${action.sessionId}.` };
    }
    if (action.kind === "stop") {
      await this.client.request("orchestration.dispatchCommand", {
        type: "thread.turn.interrupt",
        commandId: randomUUID(),
        threadId: action.sessionId,
        createdAt: now,
      });
      return { ok: true, message: `Stopped ${action.sessionId}.` };
    }
    if (action.kind === "approve" || action.kind === "decline") {
      await this.client.request("orchestration.dispatchCommand", {
        type: "thread.approval.respond",
        commandId: randomUUID(),
        threadId: action.sessionId,
        requestId: action.text,
        decision: action.kind === "approve" ? "accept" : "decline",
        createdAt: now,
      });
      return { ok: true, message: `${action.kind === "approve" ? "Approved" : "Declined"} ${action.sessionId}.` };
    }
    if (action.kind === "new") {
      const project = this.projects.get(action.sessionId);
      const defaultSelection = project?.defaultModelSelection || readDefaultModelSelection();
      if (!project) throw new Error(`Project ${action.sessionId} is no longer available.`);
      if (!defaultSelection) throw new Error("No default model is configured for this project.");
      const threadId = randomUUID();
      const title = firstLine(action.text);
      await this.client.request("orchestration.dispatchCommand", {
        type: "thread.turn.start",
        commandId: randomUUID(),
        threadId,
        message: {
          messageId: randomUUID(),
          role: "user",
          text: action.text,
          attachments: [],
        },
        modelSelection: defaultSelection,
        titleSeed: title,
        runtimeMode: "full-access",
        interactionMode: "default",
        bootstrap: {
          createThread: {
            projectId: project.id,
            title,
            modelSelection: defaultSelection,
            runtimeMode: "full-access",
            interactionMode: "default",
            branch: null,
            worktreePath: null,
            createdAt: now,
          },
        },
        createdAt: now,
      });
      return { ok: true, message: `Started ${title} in ${project.title}.`, threadId };
    }
    if (action.kind === "new_project") {
      const workspaceRoot = String(action.workspaceRoot || "").trim();
      const title = String(action.title || "").trim() || path.basename(workspaceRoot);
      if (!path.isAbsolute(workspaceRoot)) throw new Error("Project folder must be an absolute path.");
      if (!title) throw new Error("Project folder must have a name.");
      const projectId = randomUUID();
      await this.client.request("orchestration.dispatchCommand", {
        type: "project.create",
        commandId: randomUUID(),
        projectId,
        title,
        workspaceRoot,
        createWorkspaceRootIfMissing: false,
        defaultModelSelection: null,
        createdAt: now,
      });
      this.dirty = true;
      await this.reconcileShell().catch(() => {});
      return { ok: true, message: `Added ${title}.`, projectId };
    }
    if (action.kind === "answer") {
      const match = action.text.match(/^([^~]+)~([^~]*)~(.*)$/s);
      if (!action.answers && !match) throw new Error("The queued answer is malformed.");
      const [, requestId, questionId, answer] = match || [];
      await this.client.request("orchestration.dispatchCommand", {
        type: "thread.user-input.respond",
        commandId: randomUUID(),
        threadId: action.sessionId,
        requestId: action.requestId || requestId,
        answers: action.answers || { [questionId]: answer },
        createdAt: now,
      });
      return { ok: true, message: `Answered ${action.sessionId}.` };
    }
    throw new Error(`Unsupported action kind ${action.kind}.`);
  }
}

function readDefaultModelSelection() {
  const settings = readJson(path.join(t3Home, "userdata", "settings.json"), {});
  return settings?.defaultModelSelection || null;
}

// One line per tool call, in the words a person would use.
function stepLabel(itemType, toolName, subject) {
  const text = String(subject || "").replace(/\s+/g, " ").trim();
  const short = (value, length = 90) => (value.length > length ? `${value.slice(0, length - 1)}…` : value);
  const file = text.replace(/^\w+:\s*/, "").replace(/^\{.*"file_path":"([^"]+)".*$/, "$1");
  const tail = (value) => value.split("/").slice(-2).join("/");
  switch (itemType) {
    case "command_execution":
      return `Ran \`${short(text.replace(/^Bash:\s*/, ""), 80)}\``;
    case "file_change":
      return `Edited ${tail(file)}`;
    case "file_read":
      return `Read ${tail(file)}`;
    case "image_view":
      return `Viewed ${tail(file)}`;
    case "web_search":
      return `Searched “${short(text.replace(/^WebSearch:\s*/, "").replace(/^\{"query":"(.*)".*$/, "$1"), 70)}”`;
    case "collab_agent_tool_call":
      return `Subagent: ${short(text, 80)}`;
    default: {
      const name = String(toolName || "").replace(/^mcp__/, "").replace(/__/g, " › ");
      if (toolName === "Read") return `Read ${tail(file)}`;
      if (toolName === "Grep" || toolName === "Glob") return `Searched files for ${short(text.replace(/^\w+:\s*/, ""), 60)}`;
      return name ? `Used ${short(name, 60)}` : short(text || "Tool call", 90);
    }
  }
}

// Messages and tool steps in time order, keeping only steps inside the window
// of messages shown: older steps belong to messages that are not on screen.
function interleave(messages, steps) {
  if (!steps.length) return messages;
  const oldest = messages[0]?.created_at || "";
  const visible = oldest ? steps.filter((step) => step.created_at >= oldest) : steps;
  return [...messages, ...visible].sort((left, right) => (left.created_at < right.created_at ? -1 : left.created_at > right.created_at ? 1 : 0));
}

// The first line of prose in a reply, without Markdown punctuation: what a
// list row can show of "what did it say".
function snippetLine(text) {
  let fenced = false;
  let line = "";
  for (const raw of String(text || "").split("\n")) {
    if (/^\s*(`{3,}|~{3,})/.test(raw)) {
      fenced = !fenced;
      continue;
    }
    if (fenced || raw.trimStart().startsWith("|")) continue;
    const candidate = raw.replace(/^\s*(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s?)/, "").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1").replace(/[`*_]/g, "").trim();
    // Prose has words; a stray brace or rule line is not a summary.
    if (/\p{L}{2,}/u.test(candidate)) {
      line = candidate;
      break;
    }
  }
  if (!line) return "";
  return line.length > 160 ? `${line.slice(0, 159)}…` : line;
}

function errorLine(text) {
  const line = String(text || "").trim().split("\n")[0].trim();
  return line.length > 140 ? `${line.slice(0, 139)}…` : line;
}

function safeTime(value) {
  const time = Date.parse(value || "");
  return Number.isFinite(time) ? time : 0;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const bridge = new Bridge();
bridge.start().catch((error) => {
  log("Fatal bridge error", error instanceof Error ? error.stack || error.message : String(error));
  process.exitCode = 1;
});
