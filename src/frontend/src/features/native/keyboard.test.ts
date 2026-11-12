import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { Capacitor } from "@capacitor/core";
import { Keyboard } from "@capacitor/keyboard";

import { restoreKeyboardOnReturn, syncKeyboardAccessoryBarWithFocus } from "./keyboard";

const { reload } = vi.hoisted(() => ({ reload: vi.fn() }));

vi.mock("@capacitor/core", () => ({
  Capacitor: { getPlatform: vi.fn() },
  registerPlugin: () => ({ reload }),
}));
vi.mock("@capacitor/keyboard", () => ({
  Keyboard: { setAccessoryBarVisible: vi.fn(), show: vi.fn() },
}));

const setAccessoryBarVisible = vi.mocked(Keyboard.setAccessoryBarVisible);
const showKeyboard = vi.mocked(Keyboard.show);

const settle = () => vi.runAllTimersAsync();

describe("syncKeyboardAccessoryBarWithFocus", () => {
  let stop: () => void;
  let subject: HTMLInputElement;
  let editor: HTMLElement;
  let linkField: HTMLInputElement;
  let button: HTMLButtonElement;

  const start = async () => {
    stop = syncKeyboardAccessoryBarWithFocus();
    await settle();
    setAccessoryBarVisible.mockClear();
  };

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    setAccessoryBarVisible.mockResolvedValue(undefined);
    reload.mockResolvedValue(undefined);
    vi.mocked(Capacitor.getPlatform).mockReturnValue("ios");
    stop = () => {};
    document.body.innerHTML = `
      <input id="subject" />
      <div class="bn-container"><div id="editor" contenteditable="true" tabindex="0"></div></div>
      <div class="bn-mobile-formatting-toolbar"><input id="link" /></div>
      <button id="button"></button>
    `;
    subject = document.querySelector<HTMLInputElement>("#subject")!;
    editor = document.querySelector<HTMLElement>("#editor")!;
    linkField = document.querySelector<HTMLInputElement>("#link")!;
    button = document.querySelector<HTMLButtonElement>("#button")!;
  });

  afterEach(() => {
    stop();
    vi.useRealTimers();
  });

  it("hides the bar at rest", async () => {
    stop = syncKeyboardAccessoryBarWithFocus();
    await settle();

    expect(setAccessoryBarVisible).toHaveBeenCalledExactlyOnceWith({ isVisible: false });
    expect(reload).not.toHaveBeenCalled();
  });

  it("does nothing outside iOS", async () => {
    vi.mocked(Capacitor.getPlatform).mockReturnValue("android");
    stop = syncKeyboardAccessoryBarWithFocus();
    subject.focus();
    await settle();

    expect(setAccessoryBarVisible).not.toHaveBeenCalled();
    expect(reload).not.toHaveBeenCalled();
  });

  it("shows the bar on a regular field, then reloads the keyboard", async () => {
    await start();
    subject.focus();
    await settle();

    expect(setAccessoryBarVisible).toHaveBeenCalledExactlyOnceWith({ isVisible: true });
    expect(reload).toHaveBeenCalledOnce();
    expect(setAccessoryBarVisible.mock.invocationCallOrder[0]).toBeLessThan(
      reload.mock.invocationCallOrder[0],
    );
  });

  it.each([
    ["a BlockNote field", () => editor],
    ["a field of the composer toolbar", () => linkField],
    ["a control that opens no keyboard", () => button],
  ])("keeps the bar hidden on %s", async (_label, target) => {
    await start();
    target().focus();
    await settle();

    expect(setAccessoryBarVisible).not.toHaveBeenCalled();
    expect(reload).not.toHaveBeenCalled();
  });

  it("hides the bar when the focus moves to a BlockNote field", async () => {
    await start();
    subject.focus();
    await settle();
    setAccessoryBarVisible.mockClear();

    editor.focus();
    await settle();

    expect(setAccessoryBarVisible).toHaveBeenCalledExactlyOnceWith({ isVisible: false });
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("hides the bar again once the keyboard is dismissed", async () => {
    await start();
    subject.focus();
    await settle();
    setAccessoryBarVisible.mockClear();

    subject.blur();
    await settle();

    expect(setAccessoryBarVisible).toHaveBeenCalledExactlyOnceWith({ isVisible: false });
  });

  it("does not touch the bar between two regular fields", async () => {
    const other = document.createElement("textarea");
    document.body.append(other);
    await start();
    subject.focus();
    await settle();
    setAccessoryBarVisible.mockClear();

    other.focus();
    await settle();

    expect(setAccessoryBarVisible).not.toHaveBeenCalled();
  });

  it("keeps following the focus on a shell without the reload plugin", async () => {
    reload.mockRejectedValue(new Error("not implemented"));
    await start();
    subject.focus();
    await settle();
    editor.focus();
    await settle();

    expect(setAccessoryBarVisible).toHaveBeenLastCalledWith({ isVisible: false });
  });

  // Nothing fails loudly if a regenerated iOS project loses the plugin: the
  // bar just stops updating while a keyboard is opening.
  describe("iOS project", () => {
    const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
    const read = (relativePath: string) =>
      readFileSync(resolve(frontendRoot, relativePath), "utf8");

    it("compiles and registers the reload plugin", () => {
      expect(read("ios/App/App.xcodeproj/project.pbxproj")).toContain(
        "/* KeyboardAccessoryPlugin.swift in Sources */,",
      );
      expect(read("ios/App/App/MainViewController.swift")).toContain(
        "registerPluginInstance(KeyboardAccessoryPlugin())",
      );
    });
  });
});

describe("restoreKeyboardOnReturn", () => {
  let editor: HTMLElement;
  let button: HTMLButtonElement;

  const leaveAndReturn = () => {
    window.dispatchEvent(new FocusEvent("blur"));
    window.dispatchEvent(new FocusEvent("focus"));
  };

  beforeEach(() => {
    vi.clearAllMocks();
    showKeyboard.mockResolvedValue(undefined);
    vi.mocked(Capacitor.getPlatform).mockReturnValue("android");
    document.body.innerHTML = `
      <div id="editor" contenteditable="true" tabindex="0"></div>
      <button id="button"></button>
    `;
    editor = document.querySelector<HTMLElement>("#editor")!;
    button = document.querySelector<HTMLButtonElement>("#button")!;
    editor.focus();
  });

  it("reopens the keyboard once back from the surface", () => {
    restoreKeyboardOnReturn();
    window.dispatchEvent(new FocusEvent("blur"));
    expect(showKeyboard).not.toHaveBeenCalled();

    window.dispatchEvent(new FocusEvent("focus"));
    expect(showKeyboard).toHaveBeenCalledOnce();
  });

  it("waits for the app to be left first", () => {
    restoreKeyboardOnReturn();
    window.dispatchEvent(new FocusEvent("focus"));

    expect(showKeyboard).not.toHaveBeenCalled();
  });

  it("reopens it only once", () => {
    restoreKeyboardOnReturn();
    leaveAndReturn();
    leaveAndReturn();

    expect(showKeyboard).toHaveBeenCalledOnce();
  });

  it("leaves the keyboard down when the field lost the focus meanwhile", () => {
    restoreKeyboardOnReturn();
    window.dispatchEvent(new FocusEvent("blur"));
    editor.blur();
    window.dispatchEvent(new FocusEvent("focus"));

    expect(showKeyboard).not.toHaveBeenCalled();
  });

  it("does nothing when no field holds the focus", () => {
    button.focus();
    restoreKeyboardOnReturn();
    leaveAndReturn();

    expect(showKeyboard).not.toHaveBeenCalled();
  });

  it("does nothing on iOS, which reopens the keyboard by itself", () => {
    vi.mocked(Capacitor.getPlatform).mockReturnValue("ios");
    restoreKeyboardOnReturn();
    leaveAndReturn();

    expect(showKeyboard).not.toHaveBeenCalled();
  });
});
