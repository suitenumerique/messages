import { Capacitor } from "@capacitor/core";
import { Keyboard } from "@capacitor/keyboard";

import { restoreKeyboardOnReturn } from "./keyboard";

vi.mock("@capacitor/core", () => ({
  Capacitor: { getPlatform: vi.fn() },
}));
vi.mock("@capacitor/keyboard", () => ({
  Keyboard: { setAccessoryBarVisible: vi.fn(), show: vi.fn() },
}));

const showKeyboard = vi.mocked(Keyboard.show);

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
