import { Search, X } from "lucide-react";
import { useEffect, useState } from "react";

interface TilePickerDrawerProps {
  open: boolean;
  onClose: () => void;
  title: string;
  /** Count of currently-selected entities, shown in the header so you know what
   *  you picked without scrolling the grid. */
  selectedCount: number;
  /** Rendered with the current search term so the caller filters its own items —
   *  the drawer does not know the entity shape. */
  children: (search: string) => React.ReactNode;
  /** Shown where the grid would be when the caller has nothing to render. */
  emptyText?: React.ReactNode;
  isEmpty: boolean;
  searchPlaceholder?: string;
  /** Optional controls between the search box and the grid — e.g. the tools
   *  picker's source filter. A slot rather than a `filters` prop with a fixed
   *  shape: knowledge bases have nothing to filter by, and the drawer is not
   *  supposed to know what entity it is showing. */
  toolbar?: React.ReactNode;
  testId?: string;
}

/** Browse-and-select drawer shell: header + search + a scrollable tile grid.
 *
 *  Deliberately a drawer over the current page rather than a route: the pickers
 *  are used mid-agent-creation, and navigating away to a separate browse page
 *  would discard the half-filled agent form.
 *
 *  Browse-and-select only — there is no create-new affordance here. Authoring a
 *  tool or knowledge base stays on its management page, which keeps this drawer
 *  free of nested form state and the "created it but forgot to select it" edge
 *  case. Consequence, accepted deliberately: someone who needs an entity that
 *  does not exist yet still has to leave. The empty state names where to go
 *  rather than linking away mid-draft. */
export default function TilePickerDrawer({
  open,
  onClose,
  title,
  selectedCount,
  children,
  emptyText,
  isEmpty,
  searchPlaceholder = "Search by name or description",
  toolbar,
  testId,
}: TilePickerDrawerProps) {
  const [search, setSearch] = useState("");

  // Reset the filter each time the drawer opens — a stale search term from last
  // time reads as "nothing available".
  useEffect(() => {
    if (open) setSearch("");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" data-testid={testId}>
      <div
        className="absolute inset-0 bg-slate-900/20"
        onClick={onClose}
        aria-hidden="true"
      />
      <div className="relative w-full max-w-2xl h-full bg-white shadow-xl flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <div>
            <h3 className="text-base font-semibold text-slate-900">{title}</h3>
            <p className="text-xs text-slate-500 mt-0.5">
              {selectedCount} selected
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <div className="px-5 py-3 border-b border-slate-100">
          <div className="relative">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
            />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="input pl-9"
              placeholder={searchPlaceholder}
            />
          </div>
          {toolbar && <div className="mt-2.5">{toolbar}</div>}
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {isEmpty ? (
            <p className="text-sm text-slate-400 italic">{emptyText}</p>
          ) : (
            /* The grid gets its own testid so a caller's toolbar (e.g. the
               tools source filter, whose buttons repeat the server names) can't
               be mistaken for a tile when querying by text. */
            <div
              className="grid grid-cols-1 sm:grid-cols-2 gap-3"
              data-testid={testId ? `${testId}-grid` : undefined}
            >
              {children(search)}
            </div>
          )}
        </div>

        <div className="px-5 py-3 border-t border-slate-200 flex justify-end">
          <button type="button" onClick={onClose} className="btn-primary">
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
