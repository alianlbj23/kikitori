import { describe, expect, it } from "vitest";

import { OLLAMA_DOWNLOAD_URL, isLocalUrl, isTranslationServerLog, translationGuide, type ServerProbe } from "./translation-guide";

const LOCAL_DOWN: ServerProbe = { base_url: "http://localhost:11434/v1", is_local: true, reachable: false, error: "refused" };
const REMOTE_DOWN: ServerProbe = { base_url: "https://llm.example/v1", is_local: false, reachable: false, error: null };
const UP: ServerProbe = { ...LOCAL_DOWN, reachable: true, error: null };
const FALLBACK = "http://localhost:11434/v1";

describe("translationGuide", () => {
  it("offers installing Ollama when the local server is unreachable", () => {
    expect(translationGuide({ translationEnabled: true, server: LOCAL_DOWN, startBlockedUnreachable: false }, FALLBACK)).toEqual({
      url: LOCAL_DOWN.base_url,
      offerInstall: true,
    });
  });

  it("only offers original-only for an unreachable remote server", () => {
    expect(translationGuide({ translationEnabled: true, server: REMOTE_DOWN, startBlockedUnreachable: false }, FALLBACK)).toEqual({
      url: REMOTE_DOWN.base_url,
      offerInstall: false,
    });
  });

  it("is hidden while the server answers, before any probe, or with translation off", () => {
    expect(translationGuide({ translationEnabled: true, server: UP, startBlockedUnreachable: false }, FALLBACK)).toBeNull();
    expect(translationGuide({ translationEnabled: true, server: null, startBlockedUnreachable: false }, FALLBACK)).toBeNull();
    expect(translationGuide({ translationEnabled: false, server: LOCAL_DOWN, startBlockedUnreachable: true }, FALLBACK)).toBeNull();
  });

  it("shows when starting stopped on an unreachable server, even without a probe", () => {
    expect(translationGuide({ translationEnabled: true, server: null, startBlockedUnreachable: true }, FALLBACK)).toEqual({
      url: FALLBACK,
      offerInstall: true,
    });
    expect(
      translationGuide({ translationEnabled: true, server: null, startBlockedUnreachable: true }, "https://llm.example/v1"),
    ).toEqual({ url: "https://llm.example/v1", offerInstall: false });
  });
});

describe("isLocalUrl", () => {
  it("recognises this computer's addresses", () => {
    for (const ok of ["http://localhost:11434/v1", "http://127.0.0.1:8080", "http://[::1]:11434"]) expect(isLocalUrl(ok)).toBe(true);
    for (const no of ["https://llm.example/v1", "http://192.168.1.2:11434", "not a url"]) expect(isLocalUrl(no)).toBe(false);
  });
});

describe("Ollama download page", () => {
  it("is the official page the opener permission allows", async () => {
    expect(OLLAMA_DOWNLOAD_URL).toBe("https://ollama.com/download");
    const capability = (await import("../../src-tauri/capabilities/main.json")).default as {
      permissions: (string | { identifier: string; allow?: { url: string }[] })[];
    };
    const opener = capability.permissions.find((p) => typeof p !== "string" && p.identifier === "opener:allow-open-url");
    expect(typeof opener === "object" && opener.allow?.map((a) => a.url)).toContain(OLLAMA_DOWNLOAD_URL);
  });
});

describe("isTranslationServerLog", () => {
  it("recognises the engine's translation-server warnings (covered by the guide)", () => {
    for (const message of [
      "keep-warm request to the translation server failed: WarmUpError: cannot reach http://localhost:11434 (repeats within 60 s are logged at debug level)",
      "translation failed: TranslationError: connection refused",
      "cannot warm up translation model qwen3.5:9b: connection refused",
      "cannot keep the translation model warm",
    ]) {
      expect(isTranslationServerLog(message), message).toBe(true);
    }
  });

  it("leaves every other engine message alone", () => {
    for (const message of [
      "the translation server rejected the API key (HTTP 401/403); check translation.api_key_env",
      "cannot switch the keep-warm task: RuntimeError: boom",
      "session 3 is still releasing its models after 5.0 s; reporting the session as ended anyway",
      "",
      "some text mentioning translation failed: in the middle",
    ]) {
      expect(isTranslationServerLog(message), message).toBe(false);
    }
  });
});
