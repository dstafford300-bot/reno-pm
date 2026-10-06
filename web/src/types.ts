export type Role = "owner" | "pm" | "contractor";
export type Status = "Pending" | "In Progress" | "Completed";

export interface Capabilities {
  view_budget: boolean;
  edit_budget: boolean;
  view_costs: boolean;
  edit_schedule: boolean;
  propose_changes: boolean;
  approve_changes: boolean;
  view_journal: boolean;
  view_materials: boolean;
  upload_sow: boolean;
  manage_users: boolean;
  manage_projects: boolean;
}

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
  must_change_password: boolean;
  capabilities: Capabilities;
}

export interface PropertySummary {
  id: string;
  property_name: string;
  address: string | null;
  archived: boolean;
  progress_percent: number;
  task_count: number;
  total_budget?: number;
}

export interface DashTask {
  id: string;
  unit_id: string;
  task_name: string;
  cost_group: string | null;
  status: Status;
  percent_complete: number;
  budgeted_cost?: number;
}

export interface PropertyDetail {
  id: string;
  property_name: string;
  address: string | null;
  archived: boolean;
  progress_percent: number;
  living_units: number;
  has_schedule: boolean;
  total_budget?: number;
  telegram_linked?: boolean;
  telegram_phrase?: string;
  units: { id: string; unit_name: string; tasks: DashTask[] }[];
}

export interface Task {
  id: string;
  unit_id: string;
  unit_name: string | null;
  task_name: string;
  cost_group: string | null;
  status: Status;
  percent_complete: number;
  start_date: string | null;
  estimated_end_date: string | null;
  dependencies: string[];
  awaiting_approval: boolean;
}

export interface ScheduleData {
  property: { id: string; property_name: string; archived: boolean };
  units: { id: string; unit_name: string }[];
  tasks: Task[];
  pending_publish_count?: number;
}

export interface TaskEditBody {
  status?: Status;
  percent_complete?: number;
  start_date?: string;
  estimated_end_date?: string;
  note?: string;
}

export interface TaskProgress {
  line_item_id: string;
  task_name: string;
  required_percent: number;
  actual_percent: number;
}

export interface Milestone {
  id: string;
  milestone_name: string;
  draw_amount: number;
  status: string;
  released_at: string | null;
  task_progress: TaskProgress[];
  eligible: boolean;
}

export interface BudgetData {
  property: { id: string; property_name: string; archived: boolean };
  total_budget: number;
  materials_logged: number;
  total_released: number;
  next_draw: { amount: number; name: string } | null;
  milestones: Milestone[];
  materials_by_unit: {
    unit_id: string;
    unit_name: string;
    spent: number;
    count: number;
    labor_plus_materials_budget: number | null;
    variance: number | null;
  }[];
  needs_unit: { id: string; store: string; amount: number; purchase_date: string }[];
  units: { id: string; unit_name: string }[];
  tasks: { id: string; label: string; budget_includes_materials: boolean }[];
}

export interface JournalGroup {
  ids: string[];
  posted_at: string;
  author_name: string;
  message_text: string | null;
  photo_file_ids: string[];
  linked_line_item_id: string | null;
}

export interface JournalData {
  property: { id: string; property_name: string; archived: boolean; telegram_linked: boolean };
  groups: JournalGroup[];
  tasks: { id: string; task_name: string }[];
}

export interface MaterialLog {
  id: string;
  store: string;
  amount: number;
  purchase_date: string;
  source: string | null;
  unit_name: string | null;
  photo_url: string | null;
  items: { description?: string; cost?: number }[];
  details: string;
}

export interface ChangeRequest {
  id: string;
  property_id: string;
  property_name: string | null;
  task_label: string;
  changes: Record<string, string | number>;
  note: string | null;
  requested_by: string | null;
  status: "pending" | "approved" | "rejected";
  created_at: string;
}

export interface AppUser {
  id: string;
  email: string;
  name: string;
  role: Role;
  active: boolean;
  property_ids: string[];
}
