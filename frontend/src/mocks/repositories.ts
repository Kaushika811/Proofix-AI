import type { SidebarRepo } from "@/components/proofix/Sidebar";

export const MOCK_REPOSITORIES: SidebarRepo[] = [
  {
    name: "vulnapi",
    runs: [{ id: "vulnapi-live", name: "Runtime Validation", status: "running", time: "now" }],
  },
  {
    name: "proofix",
    runs: [
      { id: "pf-1", name: "Authentication Fix", status: "completed", time: "2h ago" },
      { id: "pf-2", name: "Runtime Validation", status: "draft", time: "1d ago" },
      { id: "pf-3", name: "SQL Injection Patch", status: "completed", time: "3d ago" },
    ],
  },
  {
    name: "llm-shield",
    runs: [
      { id: "ls-1", name: "Prompt Injection Fix", status: "completed", time: "5h ago" },
      { id: "ls-2", name: "Token Validation", status: "failed", time: "2d ago" },
    ],
  },
  {
    name: "QuantumRisk",
    runs: [{ id: "qr-1", name: "Memory Leak Repair", status: "completed", time: "1w ago" }],
  },
  {
    name: "e-doc-assistant",
    runs: [{ id: "ed-1", name: "OCR Pipeline Repair", status: "draft", time: "2w ago" }],
  },
];

export const RUN_NAME_POOL = [
  "Runtime Validation",
  "Authentication Fix",
  "JWT Repair",
  "SQL Injection Patch",
  "Token Validation",
  "Memory Leak Repair",
];
