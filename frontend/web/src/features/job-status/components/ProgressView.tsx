/**
 * Progress visualization component.
 *
 * Displays real-time conversion progress received via SSE.
 * Shows each pipeline stage with status indicators and a progress bar.
 */

import type { StepName } from "../types";

interface ProgressViewProps {
  currentStep: StepName | null;
  progress: number;
  isError: boolean;
  errorMessage: string | null;
}

interface StepDefinition {
  key: StepName;
  label: string;
}

const STEPS: StepDefinition[] = [
  { key: "tex_fetch", label: "TeX取得" },
  { key: "tex2markdown", label: "Markdown変換" },
  { key: "translation", label: "翻訳" },
  { key: "summary", label: "要約" },
  { key: "packaging", label: "パッケージ作成" },
];

type StepStatus = "pending" | "active" | "completed";

function getStepStatus(
  stepKey: StepName,
  currentStep: StepName | null,
  isComplete: boolean,
): StepStatus {
  if (!currentStep) return "pending";

  const currentIndex = STEPS.findIndex((s) => s.key === currentStep);
  const stepIndex = STEPS.findIndex((s) => s.key === stepKey);

  if (isComplete || stepIndex < currentIndex) return "completed";
  if (stepIndex === currentIndex) return "active";
  return "pending";
}

function StepIndicator({
  label,
  status,
}: {
  label: string;
  status: StepStatus;
}) {
  const baseClasses = "flex items-center gap-2 text-sm py-1";

  if (status === "completed") {
    return (
      <div className={`${baseClasses} text-green-600`}>
        <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
          <path
            fillRule="evenodd"
            d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
            clipRule="evenodd"
          />
        </svg>
        <span className="font-medium">{label}</span>
      </div>
    );
  }

  if (status === "active") {
    return (
      <div className={`${baseClasses} text-blue-600`}>
        <svg
          className="w-5 h-5 animate-spin"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          />
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
          />
        </svg>
        <span className="font-medium">{label}</span>
      </div>
    );
  }

  return (
    <div className={`${baseClasses} text-gray-400`}>
      <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
        <circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" strokeWidth="2" />
      </svg>
      <span>{label}</span>
    </div>
  );
}

export default function ProgressView({
  currentStep,
  progress,
  isError,
  errorMessage,
}: ProgressViewProps) {
  const isComplete = progress >= 100 && !isError;

  return (
    <div className="w-full max-w-2xl mx-auto space-y-6">
      {/* Progress bar */}
      <div>
        <div className="flex justify-between text-sm text-gray-600 mb-1">
          <span>進捗</span>
          <span>{Math.round(progress)}%</span>
        </div>
        <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ease-out ${
              isError
                ? "bg-red-500"
                : isComplete
                  ? "bg-green-500"
                  : "bg-blue-500"
            }`}
            style={{ width: `${Math.min(progress, 100)}%` }}
          />
        </div>
      </div>

      {/* Step indicators */}
      <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-1">
        {STEPS.map((step) => (
          <StepIndicator
            key={step.key}
            label={step.label}
            status={getStepStatus(step.key, currentStep, isComplete)}
          />
        ))}
      </div>

      {/* Error display */}
      {isError && errorMessage && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
          <p className="font-medium">エラー</p>
          <p className="mt-1">{errorMessage}</p>
        </div>
      )}
    </div>
  );
}
