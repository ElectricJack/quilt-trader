import { useEffect, useRef, useState, useId } from "react";

interface JsonTextFieldProps {
  label: string;
  value: Record<string, unknown> | null;
  onChange: (parsed: Record<string, unknown> | null) => void;
  disabled?: boolean;
  placeholder?: string;
  rows?: number;
  required?: boolean;
}

export function JsonTextField({
  label,
  value,
  onChange,
  disabled,
  placeholder,
  rows = 6,
  required,
}: JsonTextFieldProps) {
  const id = useId();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const timer = useRef<number | null>(null);
  const initialText = value === null ? "" : JSON.stringify(value, null, 2);

  // Re-hydrate textarea when the value prop changes from the outside
  // (e.g. modal opens with a fresh default). We set the DOM value directly
  // so that React does not create/update a text-node child — this keeps
  // getByText queries from unexpectedly matching textarea content.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    const next = value === null ? "" : JSON.stringify(value, null, 2);
    el.value = next;
    setError(null);
    // Reset dirty state when value is cleared from outside (e.g. modal reset)
    if (value === null) setDirty(false);
  }, [value]);

  const onTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const next = e.target.value;
    setDirty(true);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      if (next.trim() === "") {
        setError(null);
        onChange(null);
        return;
      }
      try {
        const parsed = JSON.parse(next);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          setError("Top-level value must be an object");
          return;
        }
        setError(null);
        onChange(parsed);
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Invalid JSON";
        setError(`Parse error: ${msg}`);
      }
    }, 200);
  };

  const valid = error === null;

  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-sm text-gray-300">
        {label}
        {required && <span className="text-red-400 ml-1">*</span>}
      </label>
      <textarea
        ref={textareaRef}
        id={id}
        rows={rows}
        defaultValue={initialText}
        onChange={onTextChange}
        disabled={disabled}
        placeholder={placeholder ?? '{"key": "value"}'}
        className={
          "w-full font-mono text-xs bg-gray-800 border rounded px-3 py-2 " +
          "text-gray-100 focus:outline-none focus:ring-1 focus:ring-indigo-500 " +
          (valid ? "border-gray-700" : "border-red-500") +
          (disabled ? " opacity-70 cursor-not-allowed" : "")
        }
      />
      {!disabled && dirty && (
        <div className="text-xs">
          {valid ? (
            <span className="text-green-400">✓ valid JSON</span>
          ) : (
            <span className="text-red-400">✗ {error}</span>
          )}
        </div>
      )}
    </div>
  );
}
