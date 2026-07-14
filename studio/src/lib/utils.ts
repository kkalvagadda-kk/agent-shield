import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// execution_shape display labels. IMPORTANT: the STORED value stays "reactive" in the
// API/DB contract (Pydantic ^(reactive|durable)$, the ck_*_execution_shape CHECK, the TS
// unions). Users see "Ephemeral" — the true antonym of "Durable" (R1). Display-only rename;
// do not change the stored value without a data migration.
export const SHAPE_LABELS: Record<string, string> = { reactive: "Ephemeral", durable: "Durable" };
export const shapeLabel = (s: string): string => SHAPE_LABELS[s] ?? s;
