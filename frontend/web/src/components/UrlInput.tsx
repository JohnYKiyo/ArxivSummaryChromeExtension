/**
 * URL input component for arXiv paper URLs.
 *
 * Provides a text input with validation for arXiv URLs
 * (arxiv.org/abs/*, arxiv.org/pdf/*) and a submit button
 * to initiate the conversion process.
 */

import { useState, type FormEvent } from "react";

interface UrlInputProps {
  onSubmit: (url: string) => void;
  disabled: boolean;
}

const ARXIV_URL_PATTERN =
  /^https?:\/\/(www\.)?arxiv\.org\/(abs|pdf)\/\d{4}\.\d{4,5}(v\d+)?(\.pdf)?$/;

function validateArxivUrl(url: string): string | null {
  if (!url.trim()) {
    return "URLを入力してください";
  }
  if (!ARXIV_URL_PATTERN.test(url.trim())) {
    return "有効なarXiv URLを入力してください（例: https://arxiv.org/abs/2301.00001）";
  }
  return null;
}

export default function UrlInput({ onSubmit, disabled }: UrlInputProps) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const validationError = validateArxivUrl(url);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);
    onSubmit(url.trim());
  }

  return (
    <form onSubmit={handleSubmit} className="w-full max-w-2xl mx-auto">
      <div className="flex flex-col gap-2">
        <label
          htmlFor="arxiv-url"
          className="text-sm font-medium text-gray-700"
        >
          arXiv論文URL
        </label>
        <div className="flex gap-2">
          <input
            id="arxiv-url"
            type="url"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              if (error) setError(null);
            }}
            placeholder="https://arxiv.org/abs/2301.00001"
            disabled={disabled}
            className={`flex-1 px-4 py-3 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-gray-100 disabled:cursor-not-allowed ${
              error ? "border-red-400" : "border-gray-300"
            }`}
          />
          <button
            type="submit"
            disabled={disabled}
            className="px-6 py-3 bg-blue-600 text-white font-medium rounded-lg text-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors"
          >
            翻訳開始
          </button>
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
      </div>
    </form>
  );
}
