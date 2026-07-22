import {
  forwardRef,
  useEffect,
  useRef,
  useState,
  type InputHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
} from "react";

interface FieldRowProps {
  label: string;
  hint?: string;
  error?: string | undefined;
  children: ReactNode;
}

export function FieldRow({ label, hint, error, children }: FieldRowProps) {
  return (
    <div>
      <label className="mb-1 block text-sm font-medium text-text">{label}</label>
      {children}
      {hint && !error && <p className="mt-1 text-xs text-muted">{hint}</p>}
      {error && (
        <p className="mt-1 text-xs text-danger" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export const inputCls =
  "block w-full min-h-[44px] rounded-card border border-white/10 bg-surface px-3 py-2 text-sm text-text placeholder:text-muted/60 focus:border-primary focus:outline-none appearance-none";

type InputProps = InputHTMLAttributes<HTMLInputElement>;

export const TextInput = forwardRef<HTMLInputElement, InputProps>(function TextInput(
  props,
  ref,
) {
  return (
    <input
      {...props}
      ref={ref}
      className={`${inputCls} ${props.className ?? ""}`}
    />
  );
});

type TextAreaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>;

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(
  function TextArea(props, ref) {
    return (
      <textarea
        {...props}
        ref={ref}
        className={`${inputCls} ${props.className ?? ""}`}
      />
    );
  },
);

interface CustomSelectOption {
  value: string;
  label: string;
}

interface CustomSelectProps {
  value: string;
  onChange: (next: string) => void;
  options: CustomSelectOption[];
  name?: string;
  ariaLabel?: string;
}

export function CustomSelect({
  value,
  onChange,
  options,
  name,
  ariaLabel,
}: CustomSelectProps) {
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(() => {
    const idx = options.findIndex((o) => o.value === value);
    return idx >= 0 ? idx : 0;
  });
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (ev: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(ev.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  useEffect(() => {
    const idx = options.findIndex((o) => o.value === value);
    if (idx >= 0) setActiveIdx(idx);
  }, [value, options]);

  useEffect(() => {
    if (open && listRef.current) {
      const el = listRef.current.querySelector<HTMLElement>(
        `[data-idx="${activeIdx}"]`,
      );
      el?.scrollIntoView({ block: "nearest" });
    }
  }, [open, activeIdx]);

  const activeOption = options[activeIdx] ?? options[0];
  const display = activeOption ? activeOption.label : value;

  const handleKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowRight") {
      e.preventDefault();
      if (!open) setOpen(true);
      else setActiveIdx((i) => Math.min(options.length - 1, i + 1));
    } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
      e.preventDefault();
      if (!open) setOpen(true);
      else setActiveIdx((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (open && options[activeIdx]) {
        onChange(options[activeIdx].value);
        setOpen(false);
      } else {
        setOpen((v) => !v);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        name={name}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={handleKey}
        className="flex w-full min-h-[44px] items-center justify-between rounded-card border border-white/10 bg-surface px-3 py-2 text-left text-sm text-text transition hover:border-white/20 focus:border-primary focus:outline-none"
      >
        <span className="truncate">{display}</span>
        <span aria-hidden="true" className="text-muted">
          ▾
        </span>
      </button>
      {open && (
        <ul
          ref={listRef}
          role="listbox"
          tabIndex={-1}
          aria-label={ariaLabel}
          className="absolute left-0 right-0 z-20 mt-1 max-h-60 overflow-y-auto rounded-card border border-white/10 bg-canvas shadow-2xl"
        >
          {options.map((opt, idx) => {
            const selected = opt.value === value;
            const active = idx === activeIdx;
            return (
              <li
                key={opt.value}
                role="option"
                aria-selected={selected}
                data-idx={idx}
                onClick={() => {
                  onChange(opt.value);
                  setOpen(false);
                }}
                onMouseEnter={() => setActiveIdx(idx)}
                className={`cursor-pointer px-3 py-2 text-sm transition ${
                  selected
                    ? "bg-primary/15 text-primary"
                    : active
                      ? "bg-surface text-text"
                      : "text-muted hover:bg-surface hover:text-text"
                }`}
              >
                {opt.label}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

type Option = { value: string; label: string };

interface RadioGroupProps {
  options: Option[];
  value: string;
  onChange: (next: string) => void;
  name: string;
}

export function RadioGroup({ options, value, onChange, name }: RadioGroupProps) {
  return (
    <div className="flex flex-wrap gap-2" role="radiogroup">
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(opt.value)}
            className={`min-h-[36px] rounded-card border px-3 py-1.5 text-sm font-medium transition ${
              active
                ? "border-primary bg-primary/15 text-primary"
                : "border-white/10 bg-surface text-muted hover:border-white/20 hover:text-text"
            }`}
          >
            {opt.label}
          </button>
        );
      })}
      <input type="hidden" name={name} value={value} />
    </div>
  );
}
