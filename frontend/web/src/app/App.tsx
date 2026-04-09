/**
 * Root application shell.
 *
 * Provides the page layout (header, footer) and renders
 * the active page component.
 */

import HomePage from "../pages/HomePage";

export default function App() {
  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-3xl mx-auto px-4 py-12">
        {/* Header */}
        <header className="text-center mb-10">
          <h1 className="text-3xl font-bold text-gray-900">
            arXiv Paper Translator
          </h1>
          <p className="mt-2 text-gray-600">
            arXiv論文を日本語に翻訳・要約します
          </p>
        </header>

        <HomePage />

        {/* Footer */}
        <footer className="mt-16 text-center text-xs text-gray-400">
          arXiv Paper Translator
        </footer>
      </div>
    </div>
  );
}
