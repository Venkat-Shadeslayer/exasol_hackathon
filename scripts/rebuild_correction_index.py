from __future__ import annotations

import json
from pathlib import Path

from scholarmotion.memory.correction_memory import CorrectionMemory


def main() -> None:
    memory = CorrectionMemory("knowledge/corrections/correction_file.md")
    index = [
        {
            "id": item.correction_id,
            "category": item.category,
            "tags": item.tags,
            "confidence": item.confidence,
        }
        for item in memory.entries
    ]
    output = Path("knowledge/corrections/correction_index.json")
    output.write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"Indexed {len(index)} corrections")


if __name__ == "__main__":
    main()
