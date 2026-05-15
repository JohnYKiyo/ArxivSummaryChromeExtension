/**
 * Download button component.
 *
 * Renders a download button that becomes active when the conversion
 * job completes. Triggers download of the ZIP artifact.
 */

interface DownloadButtonProps {
  downloadUrl: string;
}

export default function DownloadButton({ downloadUrl }: DownloadButtonProps) {
  return (
    <div className="w-full max-w-2xl mx-auto text-center">
      <a
        href={downloadUrl}
        download
        className="inline-flex items-center gap-2 px-8 py-3 bg-green-600 text-white font-medium rounded-lg text-sm hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-green-500 focus:ring-offset-2 transition-colors"
      >
        <svg
          className="w-5 h-5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
          />
        </svg>
        ダウンロード
      </a>
      <p className="mt-3 text-sm text-gray-500">
        翻訳結果をZIPファイルとしてダウンロードします
      </p>
    </div>
  );
}
