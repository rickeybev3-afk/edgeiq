import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useHashScroll } from "./useHashScroll";

function setHash(hash: string) {
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: { ...window.location, hash, pathname: "/settings", search: "" },
  });
}

function clearHash() {
  setHash("");
}

function makeFakeElement() {
  const el = document.createElement("div");
  el.scrollIntoView = vi.fn();
  return el;
}

describe("useHashScroll", () => {
  let getElementByIdSpy: ReturnType<typeof vi.spyOn>;
  let replaceStateSpy: ReturnType<typeof vi.spyOn>;
  let fakeEl: HTMLDivElement & { scrollIntoView: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    vi.useFakeTimers();

    fakeEl = makeFakeElement() as HTMLDivElement & {
      scrollIntoView: ReturnType<typeof vi.fn>;
    };

    getElementByIdSpy = vi
      .spyOn(document, "getElementById")
      .mockReturnValue(fakeEl);

    replaceStateSpy = vi
      .spyOn(history, "replaceState")
      .mockImplementation(() => {});
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    clearHash();
  });

  it("scrolls into view when a known hash is in the URL", () => {
    setHash("#alerts");

    renderHook(() => useHashScroll(["#alerts"]));

    expect(getElementByIdSpy).toHaveBeenCalledWith("alerts");
    expect(fakeEl.scrollIntoView).toHaveBeenCalledOnce();
    expect(fakeEl.scrollIntoView).toHaveBeenCalledWith({
      behavior: "smooth",
      block: "start",
    });
  });

  it("clears the hash from the URL after ~1500 ms", () => {
    setHash("#alerts");

    renderHook(() => useHashScroll(["#alerts"]));

    expect(replaceStateSpy).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1500);

    expect(replaceStateSpy).toHaveBeenCalledOnce();
    expect(replaceStateSpy).toHaveBeenCalledWith(
      null,
      "",
      "/settings",
    );
  });

  it("does not clear the hash before 1500 ms have elapsed", () => {
    setHash("#alerts");

    renderHook(() => useHashScroll(["#alerts"]));

    vi.advanceTimersByTime(1499);

    expect(replaceStateSpy).not.toHaveBeenCalled();
  });

  it("does not trigger a second scroll on re-render for the same hash", () => {
    setHash("#alerts");

    const { rerender } = renderHook(
      ({ deps }: { deps: unknown[] }) => useHashScroll(["#alerts"], deps),
      { initialProps: { deps: [false] } },
    );

    expect(fakeEl.scrollIntoView).toHaveBeenCalledOnce();

    rerender({ deps: [true] });

    expect(fakeEl.scrollIntoView).toHaveBeenCalledOnce();
  });

  it("ignores unknown hashes", () => {
    setHash("#unknown-section");

    renderHook(() => useHashScroll(["#alerts", "#thresholds"]));

    expect(fakeEl.scrollIntoView).not.toHaveBeenCalled();
    expect(replaceStateSpy).not.toHaveBeenCalled();
  });

  it("does nothing when no hash is present", () => {
    clearHash();

    renderHook(() => useHashScroll(["#alerts"]));

    expect(fakeEl.scrollIntoView).not.toHaveBeenCalled();
    expect(replaceStateSpy).not.toHaveBeenCalled();
  });

  it("does nothing when the element is not found in the DOM", () => {
    setHash("#alerts");
    getElementByIdSpy.mockReturnValue(null);

    renderHook(() => useHashScroll(["#alerts"]));

    expect(replaceStateSpy).not.toHaveBeenCalled();
  });

  it("cancels the hash-clear timer if the component unmounts before 1500 ms", () => {
    setHash("#alerts");

    const { unmount } = renderHook(() => useHashScroll(["#alerts"]));

    vi.advanceTimersByTime(1000);
    unmount();
    vi.advanceTimersByTime(1000);

    expect(replaceStateSpy).not.toHaveBeenCalled();
  });
});
