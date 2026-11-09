import { beforeEach, describe, expect, it } from "vitest";

import { downloadErrorText } from "./download-error";
import { setLocale } from "./i18n";

describe("downloadErrorText", () => {
  beforeEach(() => setLocale("zh-TW"));

  it("tells the user to update Ollama when the model needs a newer one", () => {
    // The message a Linux machine with an old Ollama got when pulling qwen3.5.
    const raw =
      "PullError: Ollama: pull model manifest: 412: The model you are attempting to pull requires a newer version of Ollama. Please download the latest version at: https://ollama.com/download";
    const text = downloadErrorText(raw);
    expect(text).toContain("Ollama");
    expect(text).toContain("https://ollama.com/download");
    expect(text).not.toContain("PullError");
  });

  it("keeps any other reason as it is", () => {
    expect(downloadErrorText("HTTP 500 from /api/pull")).toBe("HTTP 500 from /api/pull");
  });

  it("names a missing reason", () => {
    expect(downloadErrorText(null)).toBe("未知原因");
    expect(downloadErrorText("  ")).toBe("未知原因");
  });
});
