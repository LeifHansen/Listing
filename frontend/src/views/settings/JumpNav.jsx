import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

/**
 * The sticky row of pills at the top of Settings, one per section. Tapping
 * one scrolls to the section; the pill for the section in view is filled.
 *
 * Highlighting watches the sections with an IntersectionObserver, guarded
 * because jsdom (the unit tests) has none — there the first pill stays
 * filled, which is also what a phone sees before it scrolls.
 */
export function JumpNav({ sections, className }) {
  const [active, setActive] = useState(sections[0]?.id);

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return undefined;
    const observer = new IntersectionObserver((entries) => {
      const visible = entries
        .filter((e) => e.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (visible[0]) setActive(visible[0].target.id.replace(/^settings-/, ""));
    }, { rootMargin: "-96px 0px -60% 0px", threshold: 0 });
    for (const s of sections) {
      const el = document.getElementById(`settings-${s.id}`);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [sections]);

  const jump = (id) => {
    setActive(id);
    const el = document.getElementById(`settings-${id}`);
    if (!el) return;
    try {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch {
      el.scrollIntoView();
    }
  };

  return (
    <nav
      aria-label="Settings sections"
      className={cn(
        "sticky top-0 z-10 -mx-4 px-4 sm:-mx-6 sm:px-6 py-2",
        "bg-bg/90 backdrop-blur border-b border-line/60",
        className,
      )}
    >
      <div className="flex items-center gap-2 overflow-x-auto">
        {sections.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => jump(s.id)}
            aria-current={active === s.id ? "location" : undefined}
            className={cn(
              "shrink-0 inline-flex items-center gap-1.5 h-9 px-3.5 rounded-full text-[13px]",
              "font-semibold cursor-pointer transition-colors duration-150 border",
              active === s.id
                ? "bg-blue text-on-accent border-blue"
                : "bg-card text-ink-secondary border-line hover:text-ink hover:border-line-strong",
            )}
          >
            {s.label}
          </button>
        ))}
      </div>
    </nav>
  );
}
