"""TexFetchAgent - arXiv source downloader.

Downloads the tar.gz source archive from arXiv for a given paper,
extracts .tex files and image assets. Handles arXiv's rate limiting
and various archive formats.
"""

from google.adk.agents import LlmAgent

from src.tools.arxiv import fetch_arxiv_paper


def fetch_arxiv_source_tool(arxiv_url: str) -> dict:
    """Fetch the TeX source and images from an arXiv paper URL.

    Downloads the source archive, extracts .tex files and images,
    and returns the combined TeX content along with image path information.

    Args:
        arxiv_url: Full arXiv URL (e.g. https://arxiv.org/abs/2301.00001).

    Returns:
        A dict with keys:
          - tex_content (str): concatenated TeX source.
          - image_paths (list[str]): relative paths of extracted images.
          - work_dir (str): path to the temporary working directory.
    """
    tex_content, image_paths, work_dir = fetch_arxiv_paper(arxiv_url)
    return {
        "tex_content": tex_content,
        "image_paths": [str(p) for p in image_paths],
        "work_dir": str(work_dir),
    }


_TEX_FETCH_INSTRUCTION = """\
You are a tool-calling agent that fetches arXiv paper sources.

When given an arXiv URL, call the fetch_arxiv_source_tool with that URL.
Return the full result exactly as received from the tool — do not summarise \
or modify the content.
"""


def create_tex_fetch_agent(model: str) -> LlmAgent:
    """Create a TexFetchAgent that downloads arXiv TeX sources.

    Args:
        model: The LLM model identifier (e.g. ``gemini-2.5-pro-preview-05-06``).

    Returns:
        A configured :class:`LlmAgent` with the fetch tool attached.
    """
    return LlmAgent(
        name="TexFetchAgent",
        model=model,
        instruction=_TEX_FETCH_INSTRUCTION,
        description="Downloads and extracts TeX source files and images from an arXiv paper URL.",
        tools=[fetch_arxiv_source_tool],
        output_key="tex_fetch_result",
    )
