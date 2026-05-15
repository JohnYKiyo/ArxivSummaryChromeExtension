export type StepName =
  | "tex_fetch"
  | "tex2markdown"
  | "translation"
  | "summary"
  | "packaging";

export interface ProgressEvent {
  step: StepName;
  progress: number;
  message: string;
}

export interface StatusResponse {
  job_id: string;
  status: string;
  current_step: StepName | null;
  progress: number;
  message: string | null;
  error: string | null;
  download_url: string | null;
}

export interface JobState {
  events: ProgressEvent[];
  currentStep: StepName | null;
  progress: number;
  isComplete: boolean;
  isError: boolean;
  errorMessage: string | null;
  downloadUrl: string | null;
}
