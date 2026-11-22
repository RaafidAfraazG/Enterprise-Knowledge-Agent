"""Fill result markers only from complete real-Azure benchmark output files."""

import argparse
import json
from pathlib import Path

from kagent.observability.reporting import markdown

START = "<!-- RESULTS:START -->"
END = "<!-- RESULTS:END -->"


def update(readme: Path, results: Path) -> bool:
    """Leave placeholders unchanged if files are absent or are fake-provider results."""
    paths = [results / f"{name}.json" for name in ("baseline", "optimized", "comparison")]
    if not all(path.exists() for path in paths):
        return False
    baseline, optimized, comparison = [json.loads(path.read_text()) for path in paths]
    if any(result.get("provider") != "azure" for result in (baseline, optimized, comparison)):
        return False
    text = readme.read_text()
    if START not in text or END not in text:
        raise ValueError("README needs RESULTS:START and RESULTS:END markers")
    content = markdown(baseline, optimized, comparison)
    metadata = baseline.get("metadata", {})
    content += (
        f"\nSource commit: `{metadata.get('commit', 'not recorded')}`; "
        f"Actions run: `{metadata.get('actions_run_id', 'not recorded')}`.\n"
    )
    content += "\nCost is an estimate based on token usage and configured rates.\n"
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    replacement = before + START + "\n" + content + END + after
    if replacement == text:
        return False
    readme.write_text(replacement)
    return True


def main() -> None:
    """Render in place, or print why no new measured table was written."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--results", type=Path, default=Path("eval/results"))
    args = parser.parse_args()
    print("README updated" if update(args.readme, args.results) else "README unchanged")


if __name__ == "__main__":
    main()
