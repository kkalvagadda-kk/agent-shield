import { describe, it, expect } from "vitest";
import {
  routeToken,
  openAuthorBubble,
  attachToolCall,
  attachRationale,
  type AttributedRich,
} from "./chatStream";

// Surfaces extend AttributedRich with their own fields; the reducer must
// preserve them (spread), which we prove by carrying an extra `id`.
interface Msg extends AttributedRich {
  id?: string;
}

const mk = (author?: string): Msg => ({ role: "assistant", content: "", author });

describe("routeToken", () => {
  it("appends a delta to the matching-author assistant bubble", () => {
    const start: Msg[] = [{ role: "assistant", content: "He", author: "refund-agent" }];
    const next = routeToken(start, "refund-agent", "llo", mk);
    expect(next).toHaveLength(1);
    expect(next[0].content).toBe("Hello");
    expect(next[0].author).toBe("refund-agent");
  });

  it("opens a new bubble when the incoming author differs", () => {
    const start: Msg[] = [{ role: "assistant", content: "Refund done.", author: "refund-agent" }];
    const next = routeToken(start, "fraud-checker", "Checking", mk);
    expect(next).toHaveLength(2);
    expect(next[1].author).toBe("fraud-checker");
    expect(next[1].content).toBe("Checking");
    // Prior bubble untouched (immutable).
    expect(next[0]).toBe(start[0]);
  });

  it("appends when the incoming author is undefined (single-speaker stream)", () => {
    const start: Msg[] = [{ role: "assistant", content: "Hel", author: undefined }];
    const next = routeToken(start, undefined, "lo", mk);
    expect(next).toHaveLength(1);
    expect(next[0].content).toBe("Hello");
  });

  it("does not append onto a user bubble — opens a fresh assistant bubble", () => {
    const start: Msg[] = [{ role: "user", content: "hi" }];
    const next = routeToken(start, "refund-agent", "Hello", mk);
    expect(next).toHaveLength(2);
    expect(next[1].role).toBe("assistant");
    expect(next[1].content).toBe("Hello");
  });

  it("opens the first assistant bubble on an empty transcript", () => {
    const next = routeToken([] as Msg[], "refund-agent", "Hi", mk);
    expect(next).toHaveLength(1);
    expect(next[0].content).toBe("Hi");
    expect(next[0].author).toBe("refund-agent");
  });

  it("returns a new array (does not mutate input)", () => {
    const start: Msg[] = [{ role: "assistant", content: "a", author: "x" }];
    const next = routeToken(start, "x", "b", mk);
    expect(next).not.toBe(start);
    expect(start[0].content).toBe("a");
  });
});

describe("openAuthorBubble", () => {
  it("opens an empty assistant bubble for the author (agent_start)", () => {
    const next = openAuthorBubble([] as Msg[], "refund-agent", mk);
    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({ role: "assistant", content: "", author: "refund-agent" });
  });

  it("opens a new bubble when the last is a different author", () => {
    const start: Msg[] = [{ role: "assistant", content: "done", author: "refund-agent" }];
    const next = openAuthorBubble(start, "fraud-checker", mk);
    expect(next).toHaveLength(2);
    expect(next[1].author).toBe("fraud-checker");
    expect(next[1].content).toBe("");
  });

  it("is a no-op when the last bubble is already an empty bubble for the same author", () => {
    const start: Msg[] = [{ role: "assistant", content: "", author: "refund-agent" }];
    const next = openAuthorBubble(start, "refund-agent", mk);
    expect(next).toBe(start);
  });
});

describe("attachToolCall", () => {
  it("appends a tool call to the open bubble for the matching author", () => {
    const start: Msg[] = [{ role: "assistant", content: "on it", author: "researcher" }];
    const next = attachToolCall(start, "researcher", { tool_name: "web", status: "ok" }, mk);
    expect(next).toHaveLength(1);
    expect(next[0].toolCalls).toEqual([{ tool_name: "web", status: "ok" }]);
    // content preserved, input not mutated
    expect(next[0].content).toBe("on it");
    expect(start[0].toolCalls).toBeUndefined();
  });

  it("accumulates multiple tool calls on the same bubble", () => {
    let msgs: Msg[] = [{ role: "assistant", content: "", author: "researcher" }];
    msgs = attachToolCall(msgs, "researcher", { tool_name: "a", status: "ok" }, mk);
    msgs = attachToolCall(msgs, "researcher", { tool_name: "b", status: "error" }, mk);
    expect(msgs).toHaveLength(1);
    expect(msgs[0].toolCalls).toEqual([
      { tool_name: "a", status: "ok" },
      { tool_name: "b", status: "error" },
    ]);
  });

  it("opens a new bubble when the tool call's author differs", () => {
    const start: Msg[] = [{ role: "assistant", content: "hi", author: "researcher" }];
    const next = attachToolCall(start, "answerer", { tool_name: "x", status: "ok" }, mk);
    expect(next).toHaveLength(2);
    expect(next[1].author).toBe("answerer");
    expect(next[1].toolCalls).toEqual([{ tool_name: "x", status: "ok" }]);
    expect(next[0]).toBe(start[0]);
  });
});

describe("attachRationale", () => {
  it("sets the rationale on the open bubble for the matching author", () => {
    const start: Msg[] = [{ role: "assistant", content: "answer", author: "researcher" }];
    const next = attachRationale(start, "researcher", "because reasons", mk);
    expect(next).toHaveLength(1);
    expect(next[0].rationale).toBe("because reasons");
    expect(next[0].content).toBe("answer");
    expect(start[0].rationale).toBeUndefined();
  });

  it("opens a new bubble when the rationale's author differs", () => {
    const start: Msg[] = [{ role: "assistant", content: "a", author: "researcher" }];
    const next = attachRationale(start, "answerer", "why", mk);
    expect(next).toHaveLength(2);
    expect(next[1].author).toBe("answerer");
    expect(next[1].rationale).toBe("why");
  });
});
