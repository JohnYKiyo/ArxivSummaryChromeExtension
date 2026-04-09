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

export interface SSEState {
  events: ProgressEvent[];
  currentStep: StepName | null;
  progress: number;
  isComplete: boolean;
  isError: boolean;
  errorMessage: string | null;
  downloadUrl: string | null;
}
