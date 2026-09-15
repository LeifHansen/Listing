/* A dialog does not throw focus out of the field the seller is typing in.
 *
 * Dialog's focus-management effect listed `onClose` in its dependencies. Every
 * call site passes an arrow declared in the component body, so `onClose` is a
 * NEW FUNCTION on every render -- and every keystroke in a dialog re-renders
 * the component that owns the input. So the effect tore down and re-ran per
 * character: the cleanup put focus back on whatever had opened the dialog, and
 * the setup scheduled a frame that moved it to the panel.
 *
 * The seller typed one character into Sign in and the rest went nowhere.
 *
 * The same effect also owns the scroll lock and the open-dialog stack, so the
 * re-run pushed and popped those per keystroke too.
 *
 * Rendered with react-dom + act like the other component tests here. The real
 * Dialog is used -- it is the subject -- so this test also covers its portal
 * and its focus trap.
 */
import { act, forwardRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { Dialog } from "@/components/ui/Dialog";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// framer-motion animates and defers mounting; the real Dialog renders its
// panel through it, so it is stood down to keep the test about focus.
//
// Two properties this stand-in must have, or the test passes against a
// broken Dialog and proves nothing:
//
//   * CACHED per tag. A proxy that builds a new component on every property
//     read hands React a different component type each render, remounting the
//     whole subtree -- which loses focus by itself, for reasons that have
//     nothing to do with the code under test.
//   * FORWARDS THE REF. Dialog focuses `panel.current`; on React 18 a plain
//     function component drops `ref`, leaving it null and the focus call a
//     silent no-op -- so the very thing being measured never happens.
const motionTags = new Map();
vi.mock("framer-motion", () => ({
  AnimatePresence: ({ children }) => children,
  motion: new Proxy({}, {
    get: (_t, tag) => {
      if (!motionTags.has(tag)) {
        const Tag = tag;
        motionTags.set(tag, forwardRef(({ children, ...props }, ref) => {
          const clean = { ...props };
          for (const k of ["initial", "animate", "exit", "transition", "layout",
                           "whileHover", "whileTap", "variants"]) delete clean[k];
          return <Tag ref={ref} {...clean}>{children}</Tag>;
        }));
      }
      return motionTags.get(tag);
    },
  }),
}));

let host, root;

afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  host = root = undefined;
});

/* A dialog with a text field, closed by an arrow declared in the body --
 * exactly how AuthDialog, SettingsView's delete-account dialog and the merge
 * picker all pass `onClose`. */
function SignInLike() {
  const [open, setOpen] = useState(true);
  const [email, setEmail] = useState("");
  const close = () => setOpen(false);   // fresh identity every render
  return (
    <Dialog open={open} onClose={close} title="Sign in">
      <input aria-label="Email" value={email}
             onChange={(e) => setEmail(e.target.value)} />
    </Dialog>
  );
}

/* Types `text` the way a person does: one character per frame, and each
 * character reaches the field only while the field still has focus.
 *
 * Both halves matter. jsdom's value setter does not care about focus, so
 * without the guard the value assertion would pass with the caret thrown
 * somewhere else -- which is the bug. And the stolen focus arrives on a
 * requestAnimationFrame, so without a frame between keystrokes every
 * character lands before the theft is delivered and the test sees nothing.
 * A human types far slower than one frame. */
async function type(el, text) {
  for (const ch of text) {
    if (document.activeElement !== el) return;
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, "value").set;
      setter.call(el, el.value + ch);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  }
}


it("keeps focus in the field for the whole address", async () => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(<SignInLike />); });
  // The dialog focuses its panel on the next frame, by design.
  await act(async () => { await new Promise((r) => setTimeout(r, 40)); });

  const field = document.body.querySelector('input[aria-label="Email"]');
  expect(field).toBeTruthy();
  act(() => field.focus());
  expect(document.activeElement).toBe(field);

  await type(field, "seller@example.com");
  // Focus survives the re-render each keystroke causes...
  await act(async () => { await new Promise((r) => setTimeout(r, 40)); });
  expect(document.activeElement).toBe(field);
  // ...and so the field holds the whole address rather than its first letter.
  expect(field.value).toBe("seller@example.com");
});
